import secrets

from fastapi import Header, HTTPException, status

from app.config import get_settings


async def require_token(authorization: str = Header(default="")) -> None:
    """Single-user bearer auth.

    Enough to keep a personal deployment private. Swap for OAuth if this ever
    needs more than one account.
    """
    settings = get_settings()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, settings.app_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
