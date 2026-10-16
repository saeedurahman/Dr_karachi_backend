"""add whatsapp_conversations table and handoff notification event

Revision ID: d3e51b2345ef
Revises: c2f40a1234de
Create Date: 2026-10-16 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3e51b2345ef'
down_revision: Union[str, None] = 'c2f40a1234de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create enum whatsappconversationmode
    op.execute("""
    DO $$ BEGIN
        CREATE TYPE whatsappconversationmode AS ENUM ('bot', 'human_agent');
    EXCEPTION
        WHEN duplicate_object THEN null;
    END $$;
    """)

    # 2. Add notificationeventtype enum value for human handoff
    op.execute("""
    ALTER TYPE notificationeventtype ADD VALUE IF NOT EXISTS 'whatsapp_human_handoff';
    """)

    # 3. Create whatsapp_conversations table
    op.execute("""
    CREATE TABLE IF NOT EXISTS whatsapp_conversations (
        phone VARCHAR(20) PRIMARY KEY,
        mode whatsappconversationmode NOT NULL DEFAULT 'bot',
        handoff_requested_at TIMESTAMP WITH TIME ZONE,
        handoff_expires_at TIMESTAMP WITH TIME ZONE,
        last_inbound_at TIMESTAMP WITH TIME ZONE,
        auto_reply_count_window_start TIMESTAMP WITH TIME ZONE,
        auto_reply_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
    );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS whatsapp_conversations CASCADE;")
    op.execute("DROP TYPE IF EXISTS whatsappconversationmode;")
