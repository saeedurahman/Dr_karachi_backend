"""add whatsapp_broadcasts and whatsapp_broadcast_recipients tables

Revision ID: e4f62c3456fa
Revises: d3e51b2345ef
Create Date: 2026-10-16 15:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e4f62c3456fa'
down_revision: Union[str, None] = 'd3e51b2345ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add branch_id to users if not present
    op.execute("""
    ALTER TABLE users ADD COLUMN IF NOT EXISTS branch_id UUID REFERENCES branches(id) ON DELETE SET NULL;
    """)

    # 2. Create enums
    op.execute("""
    DO $$ BEGIN
        CREATE TYPE whatsappbroadcaststatus AS ENUM ('draft', 'scheduled', 'sending', 'completed', 'cancelled', 'failed');
    EXCEPTION
        WHEN duplicate_object THEN null;
    END $$;
    """)

    op.execute("""
    DO $$ BEGIN
        CREATE TYPE whatsappbroadcastrecipientstatus AS ENUM ('pending', 'sent', 'failed', 'skipped');
    EXCEPTION
        WHEN duplicate_object THEN null;
    END $$;
    """)

    # 3. Create whatsapp_broadcasts table
    op.execute("""
    CREATE TABLE IF NOT EXISTS whatsapp_broadcasts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name VARCHAR(255) NOT NULL,
        template_key VARCHAR(100) NOT NULL,
        language VARCHAR(10) NOT NULL DEFAULT 'en',
        params JSONB NOT NULL DEFAULT '{}'::jsonb,
        segment_filters JSONB NOT NULL DEFAULT '{}'::jsonb,
        branch_id UUID REFERENCES branches(id) ON DELETE SET NULL,
        status whatsappbroadcaststatus NOT NULL DEFAULT 'draft',
        scheduled_at TIMESTAMP WITH TIME ZONE,
        started_at TIMESTAMP WITH TIME ZONE,
        completed_at TIMESTAMP WITH TIME ZONE,
        total_recipients INTEGER NOT NULL DEFAULT 0,
        created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS ix_whatsapp_broadcasts_status ON whatsapp_broadcasts(status);
    CREATE INDEX IF NOT EXISTS ix_whatsapp_broadcasts_branch_id ON whatsapp_broadcasts(branch_id);
    CREATE INDEX IF NOT EXISTS ix_whatsapp_broadcasts_created_at ON whatsapp_broadcasts(created_at);
    """)

    # 4. Create whatsapp_broadcast_recipients table
    op.execute("""
    CREATE TABLE IF NOT EXISTS whatsapp_broadcast_recipients (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        broadcast_id UUID NOT NULL REFERENCES whatsapp_broadcasts(id) ON DELETE CASCADE,
        phone VARCHAR(20) NOT NULL,
        user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        status whatsappbroadcastrecipientstatus NOT NULL DEFAULT 'pending',
        skip_reason VARCHAR(255),
        message_id UUID REFERENCES whatsapp_messages(id) ON DELETE SET NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        CONSTRAINT uq_broadcast_recipients_broadcast_phone UNIQUE (broadcast_id, phone)
    );
    CREATE INDEX IF NOT EXISTS ix_whatsapp_broadcast_recipients_status ON whatsapp_broadcast_recipients(status);
    CREATE INDEX IF NOT EXISTS ix_whatsapp_broadcast_recipients_broadcast_id ON whatsapp_broadcast_recipients(broadcast_id);
    CREATE INDEX IF NOT EXISTS ix_whatsapp_broadcast_recipients_phone ON whatsapp_broadcast_recipients(phone);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS whatsapp_broadcast_recipients CASCADE;")
    op.execute("DROP TABLE IF EXISTS whatsapp_broadcasts CASCADE;")
    op.execute("DROP TYPE IF EXISTS whatsappbroadcastrecipientstatus;")
    op.execute("DROP TYPE IF EXISTS whatsappbroadcaststatus;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS branch_id;")
