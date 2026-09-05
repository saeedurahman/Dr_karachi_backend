"""
Import all models here so SQLAlchemy can resolve relationships
and Alembic can discover all tables for autogenerate.
"""
from app.models.user import User, UserRole
from app.models.refresh_token import RefreshToken
from app.models.branch import Branch
from app.models.doctor import Doctor, DoctorBranch, DoctorAvailability, DayOfWeek
from app.models.category import Category
from app.models.product import Product, BranchStock
from app.models.cart import CartItem
from app.models.order import Order, OrderItem, OrderStatus, PaymentMethod, VALID_STATUS_TRANSITIONS
from app.models.appointment import Appointment, AppointmentStatus
from app.models.lab_test import LabTest
from app.models.lab_report import LabReport, ReportFileType
from app.models.review import Review, ReviewTargetType
from app.models.blog import BlogPost
from app.models.franchise import FranchiseLead, FranchiseLeadStatus, InvestmentRange
from app.models.notification import NotificationEvent, NotificationEventType

__all__ = [
    "User", "UserRole",
    "RefreshToken",
    "Branch",
    "Doctor", "DoctorBranch", "DoctorAvailability", "DayOfWeek",
    "Category",
    "Product", "BranchStock",
    "CartItem",
    "Order", "OrderItem", "OrderStatus", "PaymentMethod",
    "Appointment", "AppointmentStatus",
    "LabTest",
    "LabReport", "ReportFileType",
    "Review", "ReviewTargetType",
    "BlogPost",
    "FranchiseLead", "FranchiseLeadStatus", "InvestmentRange",
    "NotificationEvent", "NotificationEventType",
]
