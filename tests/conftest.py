"""
Shared test configuration and fixtures.

PostgreSQL Requirement:
  Tests (especially concurrency tests in test_concurrency.py) run against
  a real PostgreSQL test database to test row-level locks (SELECT ... FOR UPDATE)
  and PostgreSQL-specific partial unique indexes.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import create_app
from app.models.user import User, UserRole
from app.utils.security import create_access_token, hash_password

# Default test database URL (configurable via TEST_DATABASE_URL env var)
TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://karachi_user:karachi_pass@localhost:5432/karachi_test_db",
)

test_engine = create_async_engine(TEST_DB_URL, echo=False, pool_pre_ping=True)
TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def setup_test_db():
    """Create all tables in test PostgreSQL database before session, drop after."""
    try:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        yield
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    except Exception as e:
        pytest.skip(f"PostgreSQL test database not accessible at {TEST_DB_URL}: {e}")


@pytest_asyncio.fixture
async def db_session(setup_test_db) -> AsyncGenerator[AsyncSession, None]:
    """Provide an isolated database session per test with automatic rollback/clean state."""
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def async_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """FastAPI async HTTP test client with get_db overridden."""
    app = create_app()

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ── Helpers for Test Data ──────────────────────────────────────────────────────
async def create_user_helper(
    session: AsyncSession,
    role: UserRole = UserRole.patient,
    phone: str | None = None,
    email: str | None = None,
) -> tuple[User, dict[str, str]]:
    """Helper to create a user and return (user, auth_headers)."""
    user_id = uuid.uuid4()
    unique_suffix = str(uuid.uuid4().hex[:6])
    phone_val = phone or f"+92300{unique_suffix}"
    email_val = email or f"user_{unique_suffix}@karachi.local"

    user = User(
        id=user_id,
        full_name=f"Test User {role.value}",
        phone=phone_val,
        email=email_val,
        password_hash=hash_password("Password123!"),
        role=role,
        is_active=True,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    token = create_access_token(user_id=user.id, role=user.role.value)
    headers = {"Authorization": f"Bearer {token}"}
    return user, headers
