from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


# ── Declarative base ───────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    """Shared declarative base — all ORM models inherit from this.

    Defined in its own module (no engine creation, no side effects) so that
    Alembic and other tooling can import it without triggering the async
    engine creation in app.database.
    """
    pass
