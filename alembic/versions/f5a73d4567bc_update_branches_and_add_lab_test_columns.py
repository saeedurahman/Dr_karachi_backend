"""update branches in place and add lab_test columns

Revision ID: f5a73d4567bc
Revises: e4f62c3456fa
Create Date: 2026-10-19 15:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f5a73d4567bc'
down_revision: Union[str, None] = 'e4f62c3456fa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Widen branches.phone to VARCHAR(100)
    op.execute("ALTER TABLE branches ALTER COLUMN phone TYPE VARCHAR(100);")

    # 2. Add columns to lab_tests
    op.execute("""
    ALTER TABLE lab_tests ADD COLUMN IF NOT EXISTS category VARCHAR(100);
    CREATE INDEX IF NOT EXISTS ix_lab_tests_category ON lab_tests (category);
    ALTER TABLE lab_tests ADD COLUMN IF NOT EXISTS specimen VARCHAR(100);
    ALTER TABLE lab_tests ADD COLUMN IF NOT EXISTS reporting_time VARCHAR(100);
    """)

    # 3. Update existing 5 branches in place (preserving primary keys for referential integrity)
    op.execute("""
    DO $$
    DECLARE
        b1_id UUID;
        b2_id UUID;
        b3_id UUID;
        b4_id UUID;
        b5_id UUID;
    BEGIN
        -- Find or assign branch 1 (ensure 11111111-1111-1111-1111-111111111111 remains valid)
        IF EXISTS (SELECT 1 FROM branches WHERE id = '11111111-1111-1111-1111-111111111111') THEN
            b1_id := '11111111-1111-1111-1111-111111111111';
        ELSE
            SELECT id INTO b1_id FROM branches ORDER BY created_at ASC LIMIT 1 OFFSET 0;
        END IF;

        IF EXISTS (SELECT 1 FROM branches WHERE id = '22222222-2222-2222-2222-222222222222') THEN
            b2_id := '22222222-2222-2222-2222-222222222222';
        ELSE
            SELECT id INTO b2_id FROM branches WHERE id <> COALESCE(b1_id, '00000000-0000-0000-0000-000000000000'::uuid) ORDER BY created_at ASC LIMIT 1 OFFSET 0;
        END IF;

        IF EXISTS (SELECT 1 FROM branches WHERE id = '33333333-3333-3333-3333-333333333333') THEN
            b3_id := '33333333-3333-3333-3333-333333333333';
        ELSE
            SELECT id INTO b3_id FROM branches WHERE id NOT IN (COALESCE(b1_id, '00000000-0000-0000-0000-000000000000'::uuid), COALESCE(b2_id, '00000000-0000-0000-0000-000000000000'::uuid)) ORDER BY created_at ASC LIMIT 1 OFFSET 0;
        END IF;

        IF EXISTS (SELECT 1 FROM branches WHERE id = '44444444-4444-4444-4444-444444444444') THEN
            b4_id := '44444444-4444-4444-4444-444444444444';
        ELSE
            SELECT id INTO b4_id FROM branches WHERE id NOT IN (COALESCE(b1_id, '00000000-0000-0000-0000-000000000000'::uuid), COALESCE(b2_id, '00000000-0000-0000-0000-000000000000'::uuid), COALESCE(b3_id, '00000000-0000-0000-0000-000000000000'::uuid)) ORDER BY created_at ASC LIMIT 1 OFFSET 0;
        END IF;

        IF EXISTS (SELECT 1 FROM branches WHERE id = '55555555-5555-5555-5555-555555555555') THEN
            b5_id := '55555555-5555-5555-5555-555555555555';
        ELSE
            SELECT id INTO b5_id FROM branches WHERE id NOT IN (COALESCE(b1_id, '00000000-0000-0000-0000-000000000000'::uuid), COALESCE(b2_id, '00000000-0000-0000-0000-000000000000'::uuid), COALESCE(b3_id, '00000000-0000-0000-0000-000000000000'::uuid), COALESCE(b4_id, '00000000-0000-0000-0000-000000000000'::uuid)) ORDER BY created_at ASC LIMIT 1 OFFSET 0;
        END IF;

        IF b1_id IS NOT NULL THEN
            UPDATE branches SET
                name = 'Quetta - Zain Plaza',
                city = 'Quetta',
                address = 'Zain Plaza, Zarghoon Road, Quetta',
                phone = '0319-1353930 / 0310-8665561',
                email = NULL,
                google_maps_url = NULL,
                working_hours = '{}'::json,
                is_active = TRUE
            WHERE id = b1_id;
        END IF;

        IF b2_id IS NOT NULL THEN
            UPDATE branches SET
                name = 'Quetta - Civil Hospital',
                city = 'Quetta',
                address = 'Nathanga Street, opposite Civil Hospital, Quetta',
                phone = '0319-1353930 / 0310-8665561',
                email = NULL,
                google_maps_url = NULL,
                working_hours = '{}'::json,
                is_active = TRUE
            WHERE id = b2_id;
        END IF;

        IF b3_id IS NOT NULL THEN
            UPDATE branches SET
                name = 'Loralai',
                city = 'Loralai',
                address = 'Tehsil Road, opposite National Bank, Loralai',
                phone = '0319-1353930 / 0310-8665561',
                email = NULL,
                google_maps_url = NULL,
                working_hours = '{}'::json,
                is_active = TRUE
            WHERE id = b3_id;
        END IF;

        IF b4_id IS NOT NULL THEN
            UPDATE branches SET
                name = 'Nushki',
                city = 'Nushki',
                address = 'Nushki Medical Center, Ghareeb Abad, Nushki',
                phone = '0319-1353930 / 0310-8665561',
                email = NULL,
                google_maps_url = NULL,
                working_hours = '{}'::json,
                is_active = TRUE
            WHERE id = b4_id;
        END IF;

        IF b5_id IS NOT NULL THEN
            UPDATE branches SET
                name = 'Muslim Bagh',
                city = 'Muslim Bagh',
                address = 'Opposite Dr. Nasiruddin Clinic, Muslim Bagh',
                phone = '0319-1353930 / 0310-8665561',
                email = NULL,
                google_maps_url = NULL,
                working_hours = '{}'::json,
                is_active = TRUE
            WHERE id = b5_id;
        END IF;
    END $$;
    """)


def downgrade() -> None:
    op.execute("""
    DROP INDEX IF EXISTS ix_lab_tests_category;
    ALTER TABLE lab_tests DROP COLUMN IF EXISTS category;
    ALTER TABLE lab_tests DROP COLUMN IF EXISTS specimen;
    ALTER TABLE lab_tests DROP COLUMN IF EXISTS reporting_time;
    ALTER TABLE branches ALTER COLUMN phone TYPE VARCHAR(20);
    """)
