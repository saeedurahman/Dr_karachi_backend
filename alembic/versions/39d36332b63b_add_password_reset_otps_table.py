"""add password_reset_otps table

Revision ID: 39d36332b63b
Revises: f5a73d4567bc
Create Date: 2026-10-22 10:45:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '39d36332b63b'
down_revision: Union[str, None] = 'f5a73d4567bc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'password_reset_otps',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=True,
        ),
        sa.Column('phone', sa.String(length=20), nullable=False),
        sa.Column('otp_hash', sa.String(length=128), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('reset_token_hash', sa.String(length=128), nullable=True),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('ip', sa.String(length=45), nullable=True),
    )
    op.create_index('ix_password_reset_otps_user_id', 'password_reset_otps', ['user_id'])
    op.create_index('ix_password_reset_otps_phone', 'password_reset_otps', ['phone'])
    op.create_index(
        'ix_password_reset_otps_reset_token_hash', 'password_reset_otps', ['reset_token_hash']
    )


def downgrade() -> None:
    op.drop_index('ix_password_reset_otps_reset_token_hash', table_name='password_reset_otps')
    op.drop_index('ix_password_reset_otps_phone', table_name='password_reset_otps')
    op.drop_index('ix_password_reset_otps_user_id', table_name='password_reset_otps')
    op.drop_table('password_reset_otps')
