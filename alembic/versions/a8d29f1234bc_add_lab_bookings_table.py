"""add lab_bookings table

Revision ID: a8d29f1234bc
Revises: 45611e933cff
Create Date: 2026-09-07 10:25:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a8d29f1234bc'
down_revision: Union[str, None] = '45611e933cff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'collectiontype') THEN
            CREATE TYPE collectiontype AS ENUM ('clinic_visit', 'home_sampling');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'labbookingstatus') THEN
            CREATE TYPE labbookingstatus AS ENUM ('pending', 'confirmed', 'sample_collected', 'completed', 'cancelled');
        END IF;
    END $$;
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS lab_bookings (
        id UUID PRIMARY KEY,
        patient_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        test_id UUID NOT NULL REFERENCES lab_tests(id) ON DELETE RESTRICT,
        branch_id UUID NOT NULL REFERENCES branches(id) ON DELETE RESTRICT,
        collection_type collectiontype NOT NULL DEFAULT 'clinic_visit',
        preferred_date DATE NOT NULL,
        time_slot VARCHAR(50) NOT NULL,
        collection_address TEXT,
        notes TEXT,
        status labbookingstatus NOT NULL DEFAULT 'pending',
        total_price NUMERIC(10, 2) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
    );
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_lab_bookings_patient_id ON lab_bookings(patient_id);
    CREATE INDEX IF NOT EXISTS ix_lab_bookings_test_id ON lab_bookings(test_id);
    CREATE INDEX IF NOT EXISTS ix_lab_bookings_branch_id ON lab_bookings(branch_id);
    CREATE INDEX IF NOT EXISTS ix_lab_bookings_status ON lab_bookings(status);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS lab_bookings CASCADE;")
    op.execute("DROP TYPE IF EXISTS labbookingstatus;")
    op.execute("DROP TYPE IF EXISTS collectiontype;")
