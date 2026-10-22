"""Auth router — /api/v1/auth"""

from fastapi import APIRouter, Request, status
import uuid

from app.dependencies import CurrentUser, DBSession, require_roles
from app.models.user import UserRole
from app.schemas.auth import (
    CreateStaffUserRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LogoutRequest,
    PatientRegisterRequest,
    RefreshTokenRequest,
    ResetPasswordConfirmRequest,
    ResetPasswordRequest,
    ResetPasswordResponse,
    TokenResponse,
    UpdateProfileRequest,
    UpdateStaffUserRequest,
    UserResponse,
    VerifyOtpRequest,
    VerifyOtpResponse,
)
from app.services.auth_service import AuthService
from app.utils.limiter import limiter

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
    if body.marketing_opt_in and body.phone:
        from app.services.whatsapp.consent_service import ConsentService
        await ConsentService.set_marketing_opt_in(
            db=db,
            phone=body.phone,
            value=True,
            source="patient_registration",
            user_id=user.id,
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
    from sqlalchemy import select
    from fastapi import HTTPException
    from app.models.branch import Branch
    from app.models.user import User
    from app.utils.security import hash_password

    if body.branch_id is not None:
        branch_result = await db.execute(select(Branch).where(Branch.id == body.branch_id))
        if not branch_result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Branch not found")

    user = User(
        full_name=body.full_name,
        phone=body.phone,
        email=body.email,
        hashed_password=hash_password(body.password),
        role=body.role,
        branch_id=body.branch_id,
    )
    db.add(user)
    await db.flush()
    return UserResponse.model_validate(user)


@router.get(
    "/admin/users",
    response_model=list[UserResponse],
    summary="[Admin] List staff and doctor accounts",
    dependencies=[require_roles(UserRole.super_admin)],
)
async def list_staff_users(
    db: DBSession,
    role: UserRole | None = None,
):
    from sqlalchemy import select
    from app.models.user import User

    query = select(User).where(User.deleted_at.is_(None))
    if role:
        query = query.where(User.role == role)
    else:
        query = query.where(User.role != UserRole.patient)
    result = await db.execute(query.order_by(User.created_at.desc()))
    return [UserResponse.model_validate(u) for u in result.scalars().all()]


@router.put(
    "/admin/users/{user_id}",
    response_model=UserResponse,
    summary="[Admin] Update staff or doctor account",
    dependencies=[require_roles(UserRole.super_admin)],
)
async def update_staff_user(
    user_id: uuid.UUID,
    body: UpdateStaffUserRequest,
    db: DBSession,
):
    from sqlalchemy import select
    from app.models.branch import Branch
    from app.models.user import User
    from fastapi import HTTPException

    user_result = await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.role == UserRole.patient:
        raise HTTPException(status_code=400, detail="Cannot edit patient accounts through this endpoint")

    if body.full_name is not None:
        user.full_name = body.full_name
    if body.email is not None:
        user.email = body.email
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if "branch_id" in body.model_fields_set:
        if body.branch_id is not None:
            branch_result = await db.execute(select(Branch).where(Branch.id == body.branch_id))
            if not branch_result.scalar_one_or_none():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Branch not found")
        user.branch_id = body.branch_id

    await db.flush()
    return UserResponse.model_validate(user)


@router.post(
    "/admin/users/{user_id}/reset-password",
    response_model=UserResponse,
    summary="[Admin] Reset password for staff or doctor account",
    dependencies=[require_roles(UserRole.super_admin)],
)
async def reset_staff_password(
    user_id: uuid.UUID,
    body: ResetPasswordRequest,
    current_user: CurrentUser,
    db: DBSession,
):
    from datetime import UTC, datetime
    from loguru import logger
    from sqlalchemy import select, update
    from app.models.refresh_token import RefreshToken
    from app.models.user import User
    from app.utils.security import hash_password
    from fastapi import HTTPException

    user_result = await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.role == UserRole.patient:
        raise HTTPException(status_code=400, detail="Cannot reset patient passwords through this endpoint")

    now = datetime.now(UTC)
    user.hashed_password = hash_password(body.password)

    # Revoke all sessions / refresh tokens
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )

    logger.info(
        "ADMIN_PASSWORD_RESET_AUDIT | endpoint=POST actor_id={} target_id={} actor_role={} target_role={} is_self_reset={} timestamp={}",
        current_user.id,
        user.id,
        current_user.role.value,
        user.role.value,
        current_user.id == user.id,
        now.isoformat(),
    )

    await db.flush()
    return UserResponse.model_validate(user)


@router.put(
    "/admin/users/{user_id}/reset-password",
    response_model=UserResponse,
    summary="[Admin] Reset password for any account (patient or staff) and revoke sessions",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager)],
)
async def admin_reset_password(
    user_id: uuid.UUID,
    body: ResetPasswordRequest,
    current_user: CurrentUser,
    db: DBSession,
):
    svc = AuthService(db)
    user = await svc.admin_reset_password(
        target_user_id=user_id,
        new_password=body.password,
        current_user=current_user,
    )
    return UserResponse.model_validate(user)


# ── Public Forgot Password Flow (WhatsApp OTP) ─────────────────────────────────
@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    status_code=status.HTTP_200_OK,
    summary="Request password reset OTP via WhatsApp (Public, rate-limited: 10/min per IP)",
)
@limiter.limit("10/minute")
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    db: DBSession,
):
    svc = AuthService(db)
    client_ip = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

    await svc.request_password_reset(
        raw_phone=body.phone,
        ip=client_ip,
    )
    return ForgotPasswordResponse()


@router.post(
    "/forgot-password/verify",
    response_model=VerifyOtpResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify 6-digit WhatsApp OTP and receive single-use reset token (Public, rate-limited: 10/min per IP)",
)
@limiter.limit("10/minute")
async def verify_otp(
    request: Request,
    body: VerifyOtpRequest,
    db: DBSession,
):
    svc = AuthService(db)
    res = await svc.verify_password_reset_otp(
        raw_phone=body.phone,
        otp=body.otp,
    )
    return VerifyOtpResponse(**res)


@router.post(
    "/reset-password",
    response_model=ResetPasswordResponse,
    status_code=status.HTTP_200_OK,
    summary="Reset password using single-use reset token and revoke existing sessions",
)
async def reset_password(
    body: ResetPasswordConfirmRequest,
    db: DBSession,
):
    svc = AuthService(db)
    await svc.reset_password_with_token(
        reset_token=body.reset_token,
        new_password=body.new_password,
    )
    return ResetPasswordResponse()


