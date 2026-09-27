"""Authentication boundary: local API key only for dev, OIDC/JWT for production."""

from typing import Any
from uuid import uuid4

import httpx
import jwt
from fastapi import HTTPException, Request, status
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import User


class AuthenticationError(HTTPException):
    def __init__(self, detail: str = "authentication required") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


def _credentials(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.headers.get("X-API-Key")


def _local_user_id(request: Request, settings: Settings, token: str) -> str | None:
    prefix = f"{settings.dev_api_key}:"
    if token.startswith(prefix):
        return token[len(prefix) :].strip() or None
    return (
        request.headers.get("X-Dev-User-Id")
        or request.headers.get("X-User-Id")
        or settings.dev_user_id
    )


async def _verify_oidc(token: str, settings: Settings) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
        key: Any = settings.jwt_public_key
        if settings.oidc_jwks_url:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(settings.oidc_jwks_url)
                response.raise_for_status()
                keys = response.json().get("keys", [])
            selected = next((item for item in keys if item.get("kid") == header.get("kid")), None)
            if selected is None:
                raise AuthenticationError("signing key not found")
            algorithm = RSAAlgorithm if str(header.get("alg", "")).startswith("RS") else ECAlgorithm
            key = algorithm.from_jwk(selected)
        if not key:
            raise AuthenticationError("OIDC verification is not configured")
        decode_kwargs: dict[str, Any] = {"algorithms": ["RS256", "ES256"]}
        if settings.jwt_audience:
            decode_kwargs["audience"] = settings.jwt_audience
        if settings.jwt_issuer:
            decode_kwargs["issuer"] = settings.jwt_issuer
        return jwt.decode(token, key=key, **decode_kwargs)
    except AuthenticationError:
        raise
    except (jwt.PyJWTError, httpx.HTTPError, ValueError) as exc:
        raise AuthenticationError("invalid bearer token") from exc


async def get_current_user(request: Request, session: AsyncSession) -> User:
    settings: Settings = request.app.state.settings
    token = _credentials(request)
    if not token:
        raise AuthenticationError()

    user_id: str | None = None
    claims: dict[str, Any] = {}
    if settings.is_local_auth_enabled and (
        token == settings.dev_api_key or token.startswith(f"{settings.dev_api_key}:")
    ):
        user_id = _local_user_id(request, settings, token)
        if not user_id:
            # A one-user local database is convenient while still avoiding an implicit
            # identity when multiple users exist.
            users = (
                (await session.execute(select(User).where(User.is_active.is_(True))))
                .scalars()
                .all()
            )
            if len(users) == 1:
                user_id = users[0].id
        if not user_id:
            raise AuthenticationError("local token needs X-Dev-User-Id")
        user = await session.scalar(
            select(User).where(User.id == user_id, User.is_active.is_(True))
        )
        if user is None:
            raise AuthenticationError("unknown local user")
        return user

    if (
        settings.environment.lower() in {"development", "dev", "test"}
        and settings.is_local_auth_enabled
    ):
        raise AuthenticationError("invalid local token")
    claims = await _verify_oidc(token, settings)
    subject = str(claims.get("sub", "")).strip()
    if not subject:
        raise AuthenticationError("token has no subject")
    user = await session.scalar(
        select(User).where(User.subject == subject, User.is_active.is_(True))
    )
    if user is None:
        email = str(claims.get("email") or f"{subject}@oidc.invalid")[:320]
        display_name = str(claims.get("name") or claims.get("preferred_username") or email)[:120]
        user = User(id=str(uuid4()), subject=subject, email=email, display_name=display_name)
        session.add(user)
        await session.flush()
    return user
