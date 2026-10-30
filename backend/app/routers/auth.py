
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import update, delete
from sqlalchemy.orm import Session

from app.audit import log_event
from app.auth import create_access_token, decode_token, revoke_token, get_current_user
from app.config import get_settings
from app.database import get_db
from app.models import User, OtpCode
from app.otp import (
    detect_channel,
    normalize_identifier,
    generate_code,
    hash_code,
    hash_ip,
    expiry,
    send_code,
    OtpDeliveryError,
)
from app.schemas import DeviceTrialRequest, TokenResponse
from app.rate_limit import limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _trial_payload(user: User) -> dict:
    expires = user.trial_started_at + timedelta(
        days=settings.TRIAL_PERIOD_DAYS
    )
    return {
        "trial_active": user.trial_active(settings.TRIAL_PERIOD_DAYS),
        "trial_expires_at": expires,
    }


@router.post("/trial", response_model=TokenResponse)
@limiter.limit(settings.RATE_LIMIT_AUTH)
def start_or_resume_trial(
    request: Request,
    response: Response,
    payload: Optional[DeviceTrialRequest] = None,
    db: Session = Depends(get_db),
):
    ip_hash = hash_ip(_client_ip(request))
    cookie_token = request.cookies.get(settings.TRIAL_COOKIE_NAME)

    user = None

    # First, identify the user using the server-managed trial cookie.
    if cookie_token:
        user = (
            db.query(User)
            .filter(User.trial_token == cookie_token)
            .first()
        )

    # If there is no cookie, look for an existing device trial.
    if not user and payload and payload.device_id:
        user = (
            db.query(User)
            .filter(User.device_id == payload.device_id)
            .first()
        )

    if not user:
        since = datetime.utcnow() - timedelta(days=1)

        recent_count = (
            db.query(User)
            .filter(
                User.trial_anchor_ip_hash == ip_hash,
                User.trial_started_at >= since,
            )
            .count()
        )

        if recent_count >= settings.TRIAL_STARTS_PER_IP_PER_DAY:
            log_event(
                db,
                "trial_rate_limited",
                outcome="failure",
                ip_hash=ip_hash,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": {
                        "code": "too_many_trials",
                        "message": (
                            "Too many trials started from this network "
                            "today. Please log in instead."
                        ),
                    }
                },
            )

        new_token = secrets.token_urlsafe(32)

        # Do not insert a device ID that may already belong to another row.
        # The server-managed cookie identifies this new trial.
        user = User(
            device_id=None,
            trial_token=new_token,
            trial_started_at=datetime.utcnow(),
            trial_anchor_ip_hash=ip_hash,
        )

        db.add(user)

        try:
            db.commit()
            db.refresh(user)
        except Exception:
            db.rollback()
            raise

        log_event(
            db,
            "trial_started",
            user_id=user.id,
            ip_hash=ip_hash,
        )

    elif not user.trial_token:
        # Add a trial token to an existing older trial record.
        user.trial_token = secrets.token_urlsafe(32)
        db.commit()
        db.refresh(user)

    response.set_cookie(
        key=settings.TRIAL_COOKIE_NAME,
        value=user.trial_token,
        max_age=settings.TRIAL_COOKIE_MAX_AGE_DAYS * 86400,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="none" if settings.COOKIE_SECURE else "lax",
        path="/",
    )

    token = create_access_token(user.id)
    trial = _trial_payload(user)

    return TokenResponse(
        access_token=token,
        user_id=user.id,
        **trial,
    )


# OTP login models

class OtpRequestIn(BaseModel):
    identifier: str = Field(..., min_length=3, max_length=255)


class OtpVerifyIn(BaseModel):
    identifier: str = Field(..., min_length=3, max_length=255)
    code: str = Field(..., min_length=4, max_length=10)
    device_id: Optional[str] = None


@router.post("/otp/request")
@limiter.limit(settings.RATE_LIMIT_AUTH)
def request_otp(
    request: Request,
    payload: OtpRequestIn,
    db: Session = Depends(get_db),
):
    channel = detect_channel(payload.identifier)

    if not channel:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "invalid_identifier",
                    "message": "Enter a valid email address or phone number.",
                }
            },
        )

    identifier = normalize_identifier(payload.identifier, channel)

    recent = (
        db.query(OtpCode)
        .filter(
            OtpCode.identifier == identifier,
            OtpCode.consumed_at.is_(None),
        )
        .order_by(OtpCode.created_at.desc())
        .first()
    )

    if recent and recent.created_at > (
        datetime.utcnow()
        - timedelta(seconds=settings.OTP_REQUEST_COOLDOWN_SECONDS)
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "code": "otp_cooldown",
                    "message": "Please wait a minute before requesting another code.",
                }
            },
        )

    code = generate_code()

    otp_row = OtpCode(
        identifier=identifier,
        channel=channel,
        code_hash=hash_code(code, identifier),
        expires_at=expiry(),
        request_ip_hash=hash_ip(_client_ip(request)),
    )

    db.add(otp_row)
    db.commit()
    db.refresh(otp_row)

    try:
        send_code(identifier, channel, code)
    except (RuntimeError, OtpDeliveryError):
        db.execute(delete(OtpCode).where(OtpCode.id == otp_row.id))
        db.commit()

        log_event(
            db,
            "otp_delivery_failed",
            outcome="failure",
            detail=channel,
            ip_hash=hash_ip(_client_ip(request)),
        )

        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "otp_delivery_unavailable",
                    "message": "We couldn't send your code. Please try again shortly.",
                }
            },
        )

    log_event(
        db,
        "otp_requested",
        outcome="success",
        detail=channel,
        ip_hash=hash_ip(_client_ip(request)),
    )

    return {
        "message": f"A verification code was sent via {channel}.",
        "channel": channel,
    }


@router.post("/otp/verify", response_model=TokenResponse)
@limiter.limit(settings.RATE_LIMIT_AUTH)
def verify_otp(
    request: Request,
    payload: OtpVerifyIn,
    db: Session = Depends(get_db),
):
    channel = detect_channel(payload.identifier)

    if not channel:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "invalid_identifier",
                    "message": "Enter a valid email address or phone number.",
                }
            },
        )

    identifier = normalize_identifier(payload.identifier, channel)

    otp = (
        db.query(OtpCode)
        .filter(
            OtpCode.identifier == identifier,
            OtpCode.consumed_at.is_(None),
        )
        .order_by(OtpCode.created_at.desc())
        .first()
    )

    if not otp or otp.expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "otp_expired",
                    "message": "Code expired or not found. Please request a new one.",
                }
            },
        )

    if otp.attempt_count >= settings.OTP_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "otp_locked",
                    "message": "Too many incorrect attempts. Please request a new code.",
                }
            },
        )

    if hash_code(payload.code, identifier) != otp.code_hash:
        db.execute(
            update(OtpCode)
            .where(OtpCode.id == otp.id)
            .values(attempt_count=OtpCode.attempt_count + 1)
        )
        db.commit()

        log_event(
            db,
            "otp_verify_failed",
            outcome="failure",
            detail=channel,
            ip_hash=hash_ip(_client_ip(request)),
        )

        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "otp_incorrect",
                    "message": "Incorrect code.",
                }
            },
        )

    result = db.execute(
        update(OtpCode)
        .where(
            OtpCode.id == otp.id,
            OtpCode.consumed_at.is_(None),
        )
        .values(consumed_at=datetime.utcnow())
    )
    db.commit()

    if result.rowcount == 0:
        log_event(
            db,
            "otp_replay_blocked",
            outcome="failure",
            detail=channel,
            ip_hash=hash_ip(_client_ip(request)),
        )

        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "otp_already_used",
                    "message": "This code has already been used. Please request a new one.",
                }
            },
        )

    if channel == "email":
        user = db.query(User).filter(User.email == identifier).first()
    else:
        user = db.query(User).filter(User.phone == identifier).first()

    # Reuse an anonymous trial record only when it is unclaimed.
    if not user and payload.device_id:
        candidate = (
            db.query(User)
            .filter(User.device_id == payload.device_id)
            .first()
        )

        if candidate and not candidate.email and not candidate.phone and not candidate.is_verified:
            user = candidate

    if not user:
        user = User()
        db.add(user)

    if channel == "email":
        user.email = identifier
    else:
        user.phone = identifier

    user.is_verified = True

    if not user.trial_token:
        user.trial_token = secrets.token_urlsafe(32)

    db.commit()
    db.refresh(user)

    log_event(
        db,
        "otp_verify_success",
        user_id=user.id,
        detail=channel,
        ip_hash=hash_ip(_client_ip(request)),
    )

    token = create_access_token(user.id)

    return TokenResponse(
        access_token=token,
        user_id=user.id,
        trial_active=True,
        trial_expires_at=None,
    )


@router.post("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    authorization = request.headers.get("authorization", "")

    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        claims = decode_token(token)

        if claims and claims.get("jti"):
            revoke_token(
                db,
                user.id,
                claims["jti"],
                claims["exp"],
            )
            log_event(db, "logout", user_id=user.id)

    response = Response(status_code=204)
    response.delete_cookie(settings.TRIAL_COOKIE_NAME, path="/")

    return response
