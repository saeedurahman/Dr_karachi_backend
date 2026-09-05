"""initial schema — complete database baseline

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-09-05 12:00:00.000000

Note for existing development databases:
  If your dev database already has tables provisioned from prior iterations,
  run:
      alembic stamp 0001_initial_schema
  This stamps your database at the current baseline without dropping existing data.
  Fresh databases can simply run:
      alembic upgrade head
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── Enums ──────────────────────────────────────────────────────────────────
    userrole_enum = sa.Enum(
        "super_admin", "branch_manager", "pharmacy_staff", "lab_staff", "doctor", "patient",
        name="userrole",
    )
    dayofweek_enum = sa.Enum(
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        name="dayofweek",
    )
    orderstatus_enum = sa.Enum(
        "pending", "confirmed", "processing", "out_for_delivery", "delivered", "cancelled", "refunded",
        name="orderstatus",
    )
    paymentmethod_enum = sa.Enum("cod", "card", "online", name="paymentmethod")
    appointmentstatus_enum = sa.Enum(
        "pending", "confirmed", "completed", "cancelled", "no_show",
        name="appointmentstatus",
    )
    reportfiletype_enum = sa.Enum("pdf", "jpeg", "png", name="reportfiletype")
    reviewtargettype_enum = sa.Enum("doctor", "service", name="reviewtargettype")
    investmentrange_enum = sa.Enum(
        "under_1m", "1m_to_3m", "3m_to_5m", "5m_to_10m", "above_10m",
        name="investmentrange",
    )
    franchiseleadstatus_enum = sa.Enum("new", "contacted", "closed", name="franchiseleadstatus")
    notificationeventtype_enum = sa.Enum(
        "order_placed", "order_status_changed", "appointment_confirmed",
        "appointment_cancelled", "report_ready", "franchise_lead_received",
        "new_review", "blog_published",
        name="notificationeventtype",
    )

    # ── 1. Users ───────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", userrole_enum, nullable=False, server_default="patient"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_phone", "users", ["phone"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_role", "users", ["role"])

    # ── 2. Refresh Tokens ──────────────────────────────────────────────────────
    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)

    # ── 3. Branches ────────────────────────────────────────────────────────────
    op.create_table(
        "branches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("contact_number", sa.String(20), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("working_hours", sa.String(255), nullable=False),
        sa.Column("services_offered", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_branches_city", "branches", ["city"])

    # ── 4. Doctors & Availability ──────────────────────────────────────────────
    op.create_table(
        "doctors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("specialization", sa.String(255), nullable=False),
        sa.Column("qualification", sa.String(500), nullable=False),
        sa.Column("experience_years", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consultation_fee", sa.Numeric(10, 2), nullable=False),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("profile_image_url", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_doctors_user_id", "doctors", ["user_id"], unique=True)
    op.create_index("ix_doctors_specialization", "doctors", ["specialization"])

    op.create_table(
        "doctor_branches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("doctor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("doctor_id", "branch_id", name="uq_doctor_branch"),
    )

    op.create_table(
        "doctor_availability",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("doctor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day_of_week", dayofweek_enum, nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("doctor_id", "branch_id", "day_of_week", "start_time", name="uq_doctor_availability_slot"),
    )

    # ── 5. Categories ──────────────────────────────────────────────────────────
    op.create_table(
        "categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon_url", sa.Text(), nullable=True),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_categories_slug_active", "categories", ["slug"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ── 6. Products & BranchStock ──────────────────────────────────────────────
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False, server_default="0.00"),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("is_prescription_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_products_sku_active", "products", ["sku"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "branch_stock",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("product_id", "branch_id", name="uq_product_branch_stock"),
        sa.CheckConstraint("stock >= 0", name="ck_stock_non_negative"),
    )

    # ── 7. Cart ────────────────────────────────────────────────────────────────
    op.create_table(
        "cart_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id", ondelete="SET NULL"), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "product_id", name="uq_user_product_cart"),
        sa.CheckConstraint("quantity > 0", name="ck_cart_quantity_positive"),
    )

    # ── 8. Orders & Order Items ────────────────────────────────────────────────
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("subtotal", sa.Numeric(10, 2), nullable=False),
        sa.Column("discount_amount", sa.Numeric(10, 2), nullable=False, server_default="0.00"),
        sa.Column("platform_fee", sa.Numeric(10, 2), nullable=False, server_default="0.00"),
        sa.Column("delivery_charges", sa.Numeric(10, 2), nullable=False, server_default="0.00"),
        sa.Column("grand_total", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", orderstatus_enum, nullable=False, server_default="pending"),
        sa.Column("payment_method", paymentmethod_enum, nullable=False, server_default="cod"),
        sa.Column("delivery_address", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "order_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("product_name_snapshot", sa.String(500), nullable=False),
        sa.Column("sku_snapshot", sa.String(100), nullable=False),
        sa.Column("price_snapshot", sa.Numeric(10, 2), nullable=False),
        sa.Column("discount_snapshot", sa.Numeric(5, 2), nullable=False, server_default="0.00"),
        sa.Column("quantity", sa.Integer(), nullable=False),
    )

    # ── 9. Appointments ────────────────────────────────────────────────────────
    op.create_table(
        "appointments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("doctor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("doctors.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("slot_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", appointmentstatus_enum, nullable=False, server_default="pending"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_appointment_doctor_slot", "appointments", ["doctor_id", "slot_datetime"])
    op.create_index(
        "uq_active_doctor_slot", "appointments", ["doctor_id", "slot_datetime"],
        unique=True, postgresql_where=sa.text("status NOT IN ('cancelled', 'no_show')"),
    )

    # ── 10. Lab Tests & Reports ────────────────────────────────────────────────
    op.create_table(
        "lab_tests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("branches.id", ondelete="SET NULL"), nullable=True),
        sa.Column("home_sampling_available", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("home_sampling_fee", sa.Numeric(10, 2), nullable=False, server_default="0.00"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("turnaround_hours", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_lab_tests_code", "lab_tests", ["code"], unique=True)

    op.create_table(
        "lab_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("test_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("lab_tests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("appointment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("file_path", sa.String(1000), nullable=False),
        sa.Column("file_type", reportfiletype_enum, nullable=False),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("appointment_id IS NOT NULL OR test_id IS NOT NULL", name="ck_lab_reports_has_linkage"),
    )

    # ── 11. Reviews ────────────────────────────────────────────────────────────
    op.create_table(
        "reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_type", reviewtargettype_enum, nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating"),
        sa.UniqueConstraint("user_id", "target_type", "target_id", name="uq_user_target_review"),
    )

    # ── 12. Blog ───────────────────────────────────────────────────────────────
    op.create_table(
        "blog_posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("slug", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("cover_image_url", sa.Text(), nullable=True),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("meta_title", sa.String(255), nullable=True),
        sa.Column("meta_description", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_blog_posts_slug_active", "blog_posts", ["slug"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ── 13. Franchise Leads ────────────────────────────────────────────────────
    op.create_table(
        "franchise_leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("investment_range", investmentrange_enum, nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("status", franchiseleadstatus_enum, nullable=False, server_default="new"),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── 14. Notification Events ────────────────────────────────────────────────
    op.create_table(
        "notification_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", notificationeventtype_enum, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("payload", postgresql.JSON(as_uuid=True), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_channel", sa.String(50), nullable=True),
        sa.Column("delivery_error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("notification_events")
    op.drop_table("franchise_leads")
    op.drop_table("blog_posts")
    op.drop_table("reviews")
    op.drop_table("lab_reports")
    op.drop_table("lab_tests")
    op.drop_table("appointments")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("cart_items")
    op.drop_table("branch_stock")
    op.drop_table("products")
    op.drop_table("categories")
    op.drop_table("doctor_availability")
    op.drop_table("doctor_branches")
    op.drop_table("doctors")
    op.drop_table("branches")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
