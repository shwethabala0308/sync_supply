"""
OTP generation, hashing and delivery — implements the authentication flow
specified in the handoff guide (email/phone OTP) rather than passwords.

Delivery is pluggable via OTP_PROVIDER so the app runs on a free tier with
no SMS/email budget (console provider logs the code) and can be switched to
a real provider in production by setting env vars only — no code change.
"""
import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger("syncsupply.otp")

class OtpDeliveryError(Exception):
    """Raised when a provider explicitly confirms (or can be confirmed to have)
    failed to deliver the code — as opposed to RuntimeError, which is raised
    for misconfiguration. Callers must not persist/keep a usable OTP record
    when this is raised; see routers/auth.py."""


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[0-9]{8,15}$")


def detect_channel(identifier: str) -> str | None:
    identifier = identifier.strip()
    if _EMAIL_RE.match(identifier):
        return "email"
    if _PHONE_RE.match(identifier):
        return "phone"
    return None


def normalize_identifier(identifier: str, channel: str) -> str:
    identifier = identifier.strip()
    return identifier.lower() if channel == "email" else identifier


def generate_code() -> str:
    # Numeric code, cryptographically random — not reconstructible from timing.
    return "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))


def hash_code(code: str, identifier: str) -> str:
    # Salted with the identifier so a leaked hash can't be replayed cross-account.
    return hashlib.sha256(f"{identifier}:{code}:{settings.JWT_SECRET}".encode()).hexdigest()


def hash_ip(ip: str) -> str:
    return hashlib.sha256(f"ip:{ip}:{settings.JWT_SECRET}".encode()).hexdigest()


def expiry() -> datetime:
    return datetime.utcnow() + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)


def send_code(identifier: str, channel: str, code: str) -> None:
    """
    Dispatches the code via the configured provider. The console provider is
    the only one wired up end-to-end here (no paid SMS/email account exists
    for this project); smtp/twilio branches show exactly what production
    wiring looks like and raise clearly if credentials are missing, instead
    of silently pretending to send.
    """
    provider = settings.OTP_PROVIDER.lower()

    if provider == "console":
        if settings.ENV.lower() == "production":
            # Startup already refuses to boot with OTP_PROVIDER=console in
            # production (see main.py), so reaching this branch in prod
            # would mean that guard was bypassed. Refuse to log a real
            # authentication code under any circumstances.
            raise RuntimeError(
                "OTP_PROVIDER=console cannot be used in production (would log live auth codes)."
            )
        logger.warning("OTP for %s (%s): %s — valid %s minutes", identifier, channel, code, settings.OTP_EXPIRE_MINUTES)
        return

    if provider == "smtp" and channel == "email":
        import smtplib
        from email.mime.text import MIMEText

        if not (settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD):
            raise RuntimeError("SMTP_HOST/SMTP_USER/SMTP_PASSWORD must be set when OTP_PROVIDER=smtp")

        msg = MIMEText(f"Your SyncSupply login code is {code}. It expires in {settings.OTP_EXPIRE_MINUTES} minutes.")
        msg["Subject"] = "Your SyncSupply login code"
        msg["From"] = settings.SMTP_FROM
        msg["To"] = identifier
        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                # send_message raises SMTPRecipientsRefused/SMTPResponseException on
                # rejection, but doesn't on a *partial* refusal with other recipients
                # accepted; there's only one recipient here, so any non-empty return
                # value means it was refused.
                refused = server.send_message(msg)
                if refused:
                    raise RuntimeError(f"SMTP server refused recipient: {refused!r}")
        except smtplib.SMTPException as exc:
            raise OtpDeliveryError(f"SMTP delivery failed: {exc}") from exc
        except OSError as exc:
            # Includes socket.timeout / connection errors.
            raise OtpDeliveryError(f"SMTP connection failed: {exc}") from exc
        return

    if provider == "resend" and channel == "email":
        import httpx

        if not settings.RESEND_API_KEY:
            raise RuntimeError("RESEND_API_KEY must be set when OTP_PROVIDER=resend")

        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                json={
                    "from": settings.RESEND_FROM,
                    "to": [identifier],
                    "subject": "Your SyncSupply login code",
                    "text": f"Your SyncSupply login code is {code}. It expires in {settings.OTP_EXPIRE_MINUTES} minutes.",
                },
                timeout=10,
            )
        except httpx.HTTPError as exc:
            raise OtpDeliveryError(f"Resend request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise OtpDeliveryError(f"Resend rejected the email (status {resp.status_code}): {resp.text[:300]}")
        return

    if provider == "twilio" and channel == "phone":
        import httpx

        if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM_NUMBER):
            raise RuntimeError("TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/TWILIO_FROM_NUMBER must be set when OTP_PROVIDER=twilio")

        url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
        try:
            resp = httpx.post(
                url,
                data={
                    "From": settings.TWILIO_FROM_NUMBER,
                    "To": identifier,
                    "Body": f"Your SyncSupply login code is {code}. It expires in {settings.OTP_EXPIRE_MINUTES} minutes.",
                },
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                timeout=10,
            )
        except httpx.HTTPError as exc:
            raise OtpDeliveryError(f"Twilio request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise OtpDeliveryError(f"Twilio rejected the message (status {resp.status_code}): {resp.text[:300]}")
        body = resp.json()
        # Twilio can accept the HTTP request (201) yet the message itself is
        # already flagged failed/undelivered in the same response for some
        # error classes (e.g. invalid "To" number formatting it still queues).
        if body.get("status") in {"failed", "undelivered"}:
            raise OtpDeliveryError(f"Twilio reported message status={body.get('status')!r}: {body.get('error_message')}")
        return

    raise RuntimeError(f"No OTP delivery configured for provider={provider!r} channel={channel!r}")
