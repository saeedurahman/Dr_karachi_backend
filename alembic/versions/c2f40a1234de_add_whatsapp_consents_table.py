"""add whatsapp_consents table

Revision ID: c2f40a1234de
Revises: b1e39f3456cd
Create Date: 2026-10-15 10:45:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'c2f40a1234de'
down_revision: Union[str, None] = 'b1e39f3456cd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS whatsapp_consents (
        phone VARCHAR(20) PRIMARY KEY,
        user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        transactional_opt_in BOOLEAN NOT NULL DEFAULT true,
        marketing_opt_in BOOLEAN NOT NULL DEFAULT false,
        opt_out_at TIMESTAMP WITH TIME ZONE,
        opt_in_source VARCHAR(100),
        history JSON NOT NULL DEFAULT '[]',
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
    );
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_whatsapp_consents_user_id ON whatsapp_consents(user_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS whatsapp_consents CASCADE;")
