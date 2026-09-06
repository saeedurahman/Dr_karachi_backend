"""Auth router — /api/v1/auth"""
from __future__ import annotations

from fastapi import APIRouter, status

from app.dependencies import CurrentUser, DBSession, require_roles
from app.models.user import UserRole
from app.schemas.auth import (
    CreateStaffUserRequest,
    LoginRequest,
    LogoutRequest,
    PatientRegisterRequest,
    RefreshTokenRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserResponse,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Patient self-registration",
)
async def register(body: PatientRegisterRequest, db: DBSession):
    svc = AuthService(db)
    user = await svc.register_patient(
        full_name=body.full_name,
        phone=body.phone,
        password=body.password,
        email=body.email,
    )
    tokens = await svc._issue_token_pair(user)
    return TokenResponse(**tokens, user=UserResponse.model_validate(user))


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login — returns access + refresh tokens",
)
async def login(body: LoginRequest, db: DBSession):
    svc = AuthService(db)
    tokens = await svc.login(phone=body.phone, password=body.password)
    # Re-fetch user for response
    from sqlalchemy import select

    from app.models.user import User
    result = await db.execute(select(User).where(User.phone == body.phone))
    user = result.scalar_one()
    return TokenResponse(**tokens, user=UserResponse.model_validate(user))


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange refresh token for new token pair",
)
async def refresh(body: RefreshTokenRequest, db: DBSession):
    svc = AuthService(db)
    tokens = await svc.refresh_tokens(body.refresh_token)
    # Decode new access token to get user_id
    import uuid

    from sqlalchemy import select

    from app.models.user import User
    from app.utils.security import decode_access_token
    payload = decode_access_token(tokens["access_token"])
    user_result = await db.execute(select(User).where(User.id == uuid.UUID(payload["sub"])))
    user = user_result.scalar_one()
    return TokenResponse(**tokens, user=UserResponse.model_validate(user))


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke refresh token (real server-side logout)",
)
async def logout(body: LogoutRequest, db: DBSession, _: CurrentUser):
    svc = AuthService(db)
    await svc.logout(body.refresh_token)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
)
async def me(current_user: CurrentUser):
    return UserResponse.model_validate(current_user)


@router.put(
    "/me",
    response_model=UserResponse,
    summary="Update own profile",
)
async def update_me(body: UpdateProfileRequest, current_user: CurrentUser, db: DBSession):
    if body.full_name:
        current_user.full_name = body.full_name
    if body.email is not None:
        current_user.email = body.email
    db.add(current_user)
    return UserResponse.model_validate(current_user)


@router.post(
    "/admin/create-user",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="[Admin] Create staff or doctor account",
    dependencies=[require_roles(UserRole.super_admin)],
)
async def create_staff_user(body: CreateStaffUserRequest, db: DBSession):
    from app.models.user import User
    from app.utils.security import hash_password

    user = User(
        full_name=body.full_name,
        phone=body.phone,
        email=body.email,
        hashed_password=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    await db.flush()
    return UserResponse.model_validate(user)
