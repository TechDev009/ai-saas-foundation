from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_session, protected_user
from ..models import User
from ..schemas import DevTokenResponse, UserCreate, UserRead

router = APIRouter(prefix="/v1", tags=["authentication"])


class DevTokenRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


@router.post(
    "/auth/dev/users", response_model=DevTokenResponse, status_code=status.HTTP_201_CREATED
)
async def create_dev_user(
    payload: UserCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> DevTokenResponse:
    settings = request.app.state.settings
    if not settings.is_local_auth_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    existing = await session.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        user = existing
    else:
        user = User(id=str(uuid4()), email=payload.email, display_name=payload.display_name)
        session.add(user)
        await session.flush()
    await session.commit()
    return DevTokenResponse(token=settings.dev_api_key, user=UserRead.model_validate(user))


@router.post("/auth/dev/token", response_model=DevTokenResponse)
async def issue_dev_token(
    payload: DevTokenRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> DevTokenResponse:
    settings = request.app.state.settings
    if not settings.is_local_auth_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    user = await session.scalar(select(User).where(User.email == payload.email.strip().lower()))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    return DevTokenResponse(token=settings.dev_api_key, user=UserRead.model_validate(user))


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(protected_user)) -> UserRead:
    return UserRead.model_validate(user)
