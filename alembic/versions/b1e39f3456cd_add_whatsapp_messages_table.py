"""add whatsapp_messages table

Revision ID: b1e39f3456cd
Revises: a8d29f1234bc
Create Date: 2026-10-15 10:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'b1e39f3456cd'
down_revision: Union[str, None] = 'a8d29f1234bc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'whatsappmessagedirection') THEN
            CREATE TYPE whatsappmessagedirection AS ENUM ('outbound', 'inbound');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'whatsappmessagetype') THEN
            CREATE TYPE whatsappmessagetype AS ENUM ('template', 'text', 'interactive', 'button', 'other');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'whatsappmessagestatus') THEN
            CREATE TYPE whatsappmessagestatus AS ENUM ('queued', 'sent', 'delivered', 'read', 'failed');
        END IF;
    END $$;
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS whatsapp_messages (
        id UUID PRIMARY KEY,
        wamid VARCHAR(255) UNIQUE,
        recipient_phone VARCHAR(20) NOT NULL,
        direction whatsappmessagedirection NOT NULL,
        message_type whatsappmessagetype NOT NULL,
        template_name VARCHAR(100),
        status whatsappmessagestatus NOT NULL DEFAULT 'queued',
        error_code INTEGER,
        error_message TEXT,
        related_entity_type VARCHAR(50),
        related_entity_id UUID,
        dedupe_key VARCHAR(255),
        sent_at TIMESTAMP WITH TIME ZONE,
        delivered_at TIMESTAMP WITH TIME ZONE,
        read_at TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
    );
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_wamid ON whatsapp_messages(wamid);
    CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_recipient_phone ON whatsapp_messages(recipient_phone);
    CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_status ON whatsapp_messages(status);
    CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_related_entity ON whatsapp_messages(related_entity_type, related_entity_id);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_messages_dedupe_key ON whatsapp_messages(dedupe_key) WHERE dedupe_key IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS whatsapp_messages CASCADE;")
    op.execute("DROP TYPE IF EXISTS whatsappmessagestatus;")
    op.execute("DROP TYPE IF EXISTS whatsappmessagetype;")
    op.execute("DROP TYPE IF EXISTS whatsappmessagedirection;")
