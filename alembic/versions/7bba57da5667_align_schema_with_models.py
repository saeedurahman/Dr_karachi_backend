"""align schema with models

Revision ID: 7bba57da5667
Revises: 0001_initial_schema
Create Date: 2026-09-06 17:30:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "7bba57da5667"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. categories: icon_url -> image_url
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'categories' AND column_name = 'icon_url') THEN
            ALTER TABLE categories RENAME COLUMN icon_url TO image_url;
        END IF;
    END $$;
    """)

    # 2. branches: phone, working_hours, services, google_maps_url
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'branches' AND column_name = 'contact_number') THEN
            ALTER TABLE branches RENAME COLUMN contact_number TO phone;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'branches' AND column_name = 'services_offered') THEN
            ALTER TABLE branches DROP COLUMN services_offered;
            ALTER TABLE branches ADD COLUMN services JSON NOT NULL DEFAULT '[]';
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'branches' AND column_name = 'working_hours' AND data_type != 'json') THEN
            ALTER TABLE branches DROP COLUMN working_hours;
            ALTER TABLE branches ADD COLUMN working_hours JSON NOT NULL DEFAULT '{}';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'branches' AND column_name = 'google_maps_url') THEN
            ALTER TABLE branches ADD COLUMN google_maps_url TEXT;
        END IF;
    END $$;
    """)

    # 3. products: is_prescription_required -> requires_prescription
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'products' AND column_name = 'is_prescription_required') THEN
            ALTER TABLE products RENAME COLUMN is_prescription_required TO requires_prescription;
        END IF;
    END $$;
    """)

    # 4. users: password_hash -> hashed_password
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'password_hash') THEN
            ALTER TABLE users RENAME COLUMN password_hash TO hashed_password;
        END IF;
    END $$;
    """)

    # 5. refresh_tokens: updated_at
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'refresh_tokens' AND column_name = 'updated_at') THEN
            ALTER TABLE refresh_tokens ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        END IF;
    END $$;
    """)

    # 6. order_items: created_at, updated_at
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'order_items' AND column_name = 'created_at') THEN
            ALTER TABLE order_items ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        END IF;
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'order_items' AND column_name = 'updated_at') THEN
            ALTER TABLE order_items ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        END IF;
    END $$;
    """)


def downgrade() -> None:
    pass
