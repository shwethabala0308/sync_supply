import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status, Header, Request
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User, RevokedToken

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    jti = str(uuid.uuid4())
    payload = {"sub": user_id, "exp": expire, "jti": jti}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Returns the full claim set (sub + jti + exp) rather than just the subject,
    since revocation checks need the jti too."""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None


def revoke_token(db: Session, user_id: str, jti: str, exp_timestamp: float) -> None:
    if not jti:
        return  # tokens minted before this change have no jti — nothing to revoke by id
    exists = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
    if exists:
        return
    db.add(RevokedToken(jti=jti, user_id=user_id, expires_at=datetime.utcfromtimestamp(exp_timestamp)))
    db.commit()


def revoke_all_tokens_for_user(db: Session, user_id: str) -> None:
    """
    Used on account deletion. We can't enumerate every JWT a user holds (JWTs
    aren't stored server-side), so this can't retroactively revoke a
    specific still-live token the way `revoke_token` does for a known jti.
    What it DOES guarantee: once the account row is gone, decode will still
    succeed but `get_current_user`'s DB lookup for that user_id returns
    nothing, so any token for a deleted account is rejected regardless of
    its jti or expiry. This function exists as the explicit, documented
    place that guarantee lives, rather than leaving it implicit.
    """
    return  # enforcement is via the "user still exists" check in get_current_user below


def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> User:
    """
    Resolves the caller to a User row — and ONLY resolves, never creates.
    New anonymous users are created exclusively by POST /api/auth/trial,
    which applies IP rate-limiting; letting any authenticated-looking
    request silently mint a fresh trial user here would bypass that limit
    entirely (that was a real gap in the previous version of this function).

    Resolution order:
    1. A valid Bearer JWT — the normal path for every request after the
       client has called /trial or /auth/otp/verify once.
    2. The server-managed trial cookie, as a fallback for a client that
       lost its token but still has the cookie (e.g. a fresh page load
       before the SPA re-authenticates).
    """
    user: Optional[User] = None

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        claims = decode_token(token)
        if claims:
            jti = claims.get("jti")
            if jti and db.query(RevokedToken).filter(RevokedToken.jti == jti).first():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"error": {"code": "session_revoked", "message": "This session has been logged out. Please log in again."}},
                )
            user = db.query(User).filter(User.id == claims.get("sub")).first()

    if not user:
        cookie_token = request.cookies.get(settings.TRIAL_COOKIE_NAME)
        if cookie_token:
            user = db.query(User).filter(User.trial_token == cookie_token).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "auth_required", "message": "Please start a trial or log in first."}},
        )

    if not user.trial_active(settings.TRIAL_PERIOD_DAYS):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"error": {"code": "trial_expired", "message": "Your 7-day trial has ended. Please log in."}},
        )

    return user
