"""
Import all models here so SQLAlchemy can resolve relationships
and Alembic can discover all tables for autogenerate.
"""
from app.models.appointment import Appointment, AppointmentStatus
from app.models.blog import BlogPost
from app.models.branch import Branch
from app.models.cart import CartItem
from app.models.category import Category
from app.models.doctor import DayOfWeek, Doctor, DoctorAvailability, DoctorBranch
from app.models.franchise import FranchiseLead, FranchiseLeadStatus, InvestmentRange
from app.models.lab_report import LabReport, ReportFileType
from app.models.lab_test import LabTest
from app.models.notification import NotificationEvent, NotificationEventType
from app.models.order import (
    VALID_STATUS_TRANSITIONS,
    Order,
    OrderItem,
    OrderStatus,
    PaymentMethod,
)
from app.models.product import BranchStock, Product
from app.models.refresh_token import RefreshToken
from app.models.review import Review, ReviewTargetType
from app.models.user import User, UserRole

__all__ = [
    "VALID_STATUS_TRANSITIONS",
    "Appointment",
    "AppointmentStatus",
    "BlogPost",
    "Branch",
    "BranchStock",
    "CartItem",
    "Category",
    "DayOfWeek",
    "Doctor",
    "DoctorAvailability",
    "DoctorBranch",
    "FranchiseLead",
    "FranchiseLeadStatus",
    "InvestmentRange",
    "LabReport",
    "LabTest",
    "NotificationEvent",
    "NotificationEventType",
    "Order",
    "OrderItem",
    "OrderStatus",
    "PaymentMethod",
    "Product",
    "RefreshToken",
    "ReportFileType",
    "Review",
    "ReviewTargetType",
    "User",
    "UserRole",
]
