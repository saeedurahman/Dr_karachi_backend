"""
FastAPI application factory.

All routers are mounted under /api/v1/ for versioning.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.routers import (
    appointments,
    auth,
    blog,
    branches,
    cart_orders,
    categories,
    doctors,
    franchise,
    lab_reports,
    lab_tests,
    products,
    reviews,
)
from app.utils.limiter import limiter

# ── OpenAPI Tags Metadata ──────────────────────────────────────────────────────
TAGS_METADATA = [
    {"name": "Auth", "description": "Patient/staff registration, JWT login, token rotation, and profile management."},
    {"name": "Branches", "description": "Branch locations, services offered, and contact information."},
    {"name": "Doctors", "description": "Doctor profiles, qualifications, and branch assignments."},
    {"name": "Pharmacy: Categories", "description": "Self-referencing category tree for mega-menu and flat listings."},
    {"name": "Pharmacy: Products", "description": "Catalog products with explicit original/discounted pricing and branch stock."},
    {"name": "Pharmacy: Cart", "description": "Single-branch shopping cart with live stock validation."},
    {"name": "Pharmacy: Orders", "description": "Order placement with concurrency-safe stock locking and status lifecycle."},
    {"name": "Lab Tests", "description": "Diagnostic test catalog with pricing and home sampling fee support."},
    {"name": "Appointments", "description": "Doctor 30-min slot generation and race-condition-safe booking."},
    {"name": "Lab Reports", "description": "Private diagnostic reports with 10-minute short-lived presigned download URLs."},
    {"name": "Reviews", "description": "Doctor and service ratings, atomic upsert, moderation, and summary scores."},
    {"name": "Blog", "description": "Health articles, auto-slug generation, featured top articles, and SEO tags."},
    {"name": "Franchise Leads", "description": "Rate-limited franchise inquiries with admin status tracking."},
]


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure upload directory exists for local storage
    from pathlib import Path
    Path(settings.LOCAL_UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    yield
    # Shutdown: nothing to clean up currently


# ── App factory ────────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="RESTful Backend API for Karachi Lab, Pharmacy, and Clinics platform.",
        openapi_tags=TAGS_METADATA,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # ── Rate limiting ──────────────────────────────────────────────────────────
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # ── CORS ───────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Static files (local storage fallback) ─────────────────────────────────
    if settings.STORAGE_BACKEND == "local":
        from pathlib import Path
        upload_dir = Path(settings.LOCAL_UPLOAD_DIR)
        upload_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/static", StaticFiles(directory=str(upload_dir)), name="static")

    # ── API v1 routers ─────────────────────────────────────────────────────────
    API_V1 = "/api/v1"
    PHARMACY = f"{API_V1}/pharmacy"

    # Phase 1
    app.include_router(auth.router, prefix=API_V1)
    app.include_router(branches.router, prefix=API_V1)
    app.include_router(doctors.router, prefix=API_V1)

    # Phase 2 — Pharmacy
    app.include_router(categories.router, prefix=PHARMACY)
    app.include_router(products.router, prefix=PHARMACY)
    app.include_router(cart_orders.cart_router, prefix=PHARMACY)
    app.include_router(cart_orders.order_router, prefix=PHARMACY)

    # Phase 3 — Lab Tests, Appointments, Lab Reports
    app.include_router(lab_tests.router, prefix=API_V1)
    app.include_router(appointments.router, prefix=API_V1)
    app.include_router(lab_reports.router, prefix=API_V1)

    # Phase 4 — Reviews, Blog, Franchise Leads
    app.include_router(reviews.router, prefix=API_V1)
    app.include_router(blog.router, prefix=API_V1)
    app.include_router(franchise.router, prefix=API_V1)

    # ── Health check ───────────────────────────────────────────────────────────
    @app.get("/health", tags=["Health"], include_in_schema=False)
    async def health():
        return {"status": "ok", "version": settings.APP_VERSION}

    return app


app = create_app()
