import asyncio
import uuid
from decimal import Decimal
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.branch import Branch
from app.models.category import Category
from app.models.product import Product, BranchStock
from app.models.user import User, UserRole
from app.utils.security import hash_password

BRANCHES_DATA = [
    {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "name": "Clifton Main Branch",
        "city": "Karachi",
        "address": "Block 5, Clifton, near Boat Basin, Karachi",
        "phone": "+922135831111",
        "email": "clifton@drkarachilab.com",
        "google_maps_url": "https://maps.google.com/?q=Clifton+Karachi",
        "working_hours": {"mon_sat": "08:00 - 23:00", "sun": "09:00 - 21:00"},
        "services": ["Pharmacy", "Diagnostic Lab", "Doctor Clinics", "Home Sampling"],
        "is_active": True,
    },
    {
        "id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
        "name": "DHA Phase 5 Branch",
        "city": "Karachi",
        "address": "26th Street, Badar Commercial Area, DHA Phase 5, Karachi",
        "phone": "+922135842222",
        "email": "dha@drkarachilab.com",
        "google_maps_url": "https://maps.google.com/?q=DHA+Phase+5+Karachi",
        "working_hours": {"mon_sat": "08:00 - 00:00", "sun": "10:00 - 22:00"},
        "services": ["Pharmacy", "Diagnostic Lab", "Home Sampling"],
        "is_active": True,
    },
    {
        "id": uuid.UUID("33333333-3333-3333-3333-333333333333"),
        "name": "Gulshan-e-Iqbal Branch",
        "city": "Karachi",
        "address": "Block 13-A, University Road, Gulshan-e-Iqbal, Karachi",
        "phone": "+922134983333",
        "email": "gulshan@drkarachilab.com",
        "google_maps_url": "https://maps.google.com/?q=Gulshan+Iqbal+Karachi",
        "working_hours": {"mon_sat": "08:00 - 23:00", "sun": "09:00 - 20:00"},
        "services": ["Pharmacy", "Diagnostic Lab", "Doctor Clinics"],
        "is_active": True,
    },
    {
        "id": uuid.UUID("44444444-4444-4444-4444-444444444444"),
        "name": "Quetta Cantt Branch",
        "city": "Quetta",
        "address": "Staff College Road, Cantonment, Quetta",
        "phone": "+92812834444",
        "email": "quettacantt@drkarachilab.com",
        "google_maps_url": "https://maps.google.com/?q=Quetta+Cantt",
        "working_hours": {"mon_sat": "08:30 - 22:00", "sun": "10:00 - 18:00"},
        "services": ["Pharmacy", "Diagnostic Lab", "Home Sampling"],
        "is_active": True,
    },
    {
        "id": uuid.UUID("55555555-5555-5555-5555-555555555555"),
        "name": "Quetta City Center Branch",
        "city": "Quetta",
        "address": "Zarghoon Road, Near Serena, Quetta",
        "phone": "+92812845555",
        "email": "quettacity@drkarachilab.com",
        "google_maps_url": "https://maps.google.com/?q=Quetta+City",
        "working_hours": {"mon_sat": "08:30 - 21:30", "sun": "Closed"},
        "services": ["Pharmacy", "Diagnostic Lab"],
        "is_active": True,
    },
]

CATEGORIES_DATA = [
    # Top-level categories
    {
        "id": uuid.UUID("a1111111-1111-1111-1111-111111111111"),
        "name": "Medicines & Prescriptions",
        "slug": "medicines-prescriptions",
        "description": "DRAP-approved prescription and OTC medicines.",
        "parent_id": None,
        "sort_order": 1,
    },
    {
        "id": uuid.UUID("a2222222-2222-2222-2222-222222222222"),
        "name": "Vitamins & Supplements",
        "slug": "vitamins-supplements",
        "description": "Daily wellness, multivitamins, minerals and immunity boosters.",
        "parent_id": None,
        "sort_order": 2,
    },
    {
        "id": uuid.UUID("a3333333-3333-3333-3333-333333333333"),
        "name": "Diabetes Care",
        "slug": "diabetes-care",
        "description": "Blood sugar monitors, strips, insulin and specialized care.",
        "parent_id": None,
        "sort_order": 3,
    },
    {
        "id": uuid.UUID("a4444444-4444-4444-4444-444444444444"),
        "name": "Personal & Baby Care",
        "slug": "personal-baby-care",
        "description": "Mother care, baby nutrition, hygiene and skin care.",
        "parent_id": None,
        "sort_order": 4,
    },
    # Sub-categories
    {
        "id": uuid.UUID("b1111111-1111-1111-1111-111111111111"),
        "name": "Pain Relief & Fever",
        "slug": "pain-relief-fever",
        "description": "Analgesics, antipyretics and anti-inflammatory medicines.",
        "parent_id": uuid.UUID("a1111111-1111-1111-1111-111111111111"),
        "sort_order": 1,
    },
    {
        "id": uuid.UUID("b2222222-2222-2222-2222-222222222222"),
        "name": "Antibiotics & Anti-Infectives",
        "slug": "antibiotics",
        "description": "Prescription antibiotics and antimicrobials.",
        "parent_id": uuid.UUID("a1111111-1111-1111-1111-111111111111"),
        "sort_order": 2,
    },
    {
        "id": uuid.UUID("b3333333-3333-3333-3333-333333333333"),
        "name": "Multivitamins & Minerals",
        "slug": "multivitamins",
        "description": "High-potency multivitamin tablets and effervescents.",
        "parent_id": uuid.UUID("a2222222-2222-2222-2222-222222222222"),
        "sort_order": 1,
    },
    {
        "id": uuid.UUID("b4444444-4444-4444-4444-444444444444"),
        "name": "Glucometers & Test Strips",
        "slug": "glucometers-strips",
        "description": "Reliable blood glucose testing devices and consumables.",
        "parent_id": uuid.UUID("a3333333-3333-3333-3333-333333333333"),
        "sort_order": 1,
    },
]

PRODUCTS_DATA = [
    {
        "id": uuid.UUID("c1111111-1111-1111-1111-111111111111"),
        "name": "Panadol Extra 500mg/65mg (Pack of 100)",
        "sku": "PAN-EXT-500",
        "description": "Paracetamol with caffeine for fast relief of tough headaches, back pain, and fever.",
        "price": 450.00,
        "discount_percent": 10.00,
        "category_id": uuid.UUID("b1111111-1111-1111-1111-111111111111"),
        "requires_prescription": False,
        "stocks": {
            "11111111-1111-1111-1111-111111111111": 50,
            "22222222-2222-2222-2222-222222222222": 45,
            "33333333-3333-3333-3333-333333333333": 30,
            "44444444-4444-4444-4444-444444444444": 25,
            "55555555-5555-5555-5555-555555555555": 20,
        },
    },
    {
        "id": uuid.UUID("c2222222-2222-2222-2222-222222222222"),
        "name": "Augmentin 625mg Tablets (Pack of 14)",
        "sku": "AUG-625-TAB",
        "description": "Co-amoxiclav broad-spectrum antibiotic for bacterial infections. Valid prescription mandatory.",
        "price": 680.00,
        "discount_percent": 5.00,
        "category_id": uuid.UUID("b2222222-2222-2222-2222-222222222222"),
        "requires_prescription": True,
        "stocks": {
            "11111111-1111-1111-1111-111111111111": 35,
            "22222222-2222-2222-2222-222222222222": 20,
            "33333333-3333-3333-3333-333333333333": 15,
            "44444444-4444-4444-4444-444444444444": 10,
            "55555555-5555-5555-5555-555555555555": 0,  # Out of stock in Quetta City to test out-of-stock UI
        },
    },
    {
        "id": uuid.UUID("c3333333-3333-3333-3333-333333333333"),
        "name": "Surbex-Z High Potency Zinc + B-Complex (30 Tablets)",
        "sku": "SUR-Z-30",
        "description": "Essential zinc and vitamin B-complex formula to support immunity and daily vitality.",
        "price": 395.00,
        "discount_percent": 0.00,
        "category_id": uuid.UUID("b3333333-3333-3333-3333-333333333333"),
        "requires_prescription": False,
        "stocks": {
            "11111111-1111-1111-1111-111111111111": 80,
            "22222222-2222-2222-2222-222222222222": 60,
            "33333333-3333-3333-3333-333333333333": 40,
            "44444444-4444-4444-4444-444444444444": 30,
            "55555555-5555-5555-5555-555555555555": 25,
        },
    },
    {
        "id": uuid.UUID("c4444444-4444-4444-4444-444444444444"),
        "name": "Accu-Chek Instant Blood Glucose Test Strips (50s)",
        "sku": "ACC-STR-50",
        "description": "Target range indicator strips with high precision results in less than 4 seconds.",
        "price": 2450.00,
        "discount_percent": 15.00,
        "category_id": uuid.UUID("b4444444-4444-4444-4444-444444444444"),
        "requires_prescription": False,
        "stocks": {
            "11111111-1111-1111-1111-111111111111": 25,
            "22222222-2222-2222-2222-222222222222": 18,
            "33333333-3333-3333-3333-333333333333": 12,
            "44444444-4444-4444-4444-444444444444": 15,
            "55555555-5555-5555-5555-555555555555": 8,
        },
    },
    {
        "id": uuid.UUID("c5555555-5555-5555-5555-555555555555"),
        "name": "Cac-1000 Plus Orange Effervescent (20 Tablets)",
        "sku": "CAC-1000-20",
        "description": "Calcium, Vitamin C, D3 and B6 effervescent drink for strong bones and energy.",
        "price": 420.00,
        "discount_percent": 8.00,
        "category_id": uuid.UUID("b3333333-3333-3333-3333-333333333333"),
        "requires_prescription": False,
        "stocks": {
            "11111111-1111-1111-1111-111111111111": 40,
            "22222222-2222-2222-2222-222222222222": 35,
            "33333333-3333-3333-3333-333333333333": 30,
            "44444444-4444-4444-4444-444444444444": 20,
            "55555555-5555-5555-5555-555555555555": 15,
        },
    },
    {
        "id": uuid.UUID("c6666666-6666-6666-6666-666666666666"),
        "name": "Glucophage 500mg Metformin (Pack of 50)",
        "sku": "GLU-500-50",
        "description": "Metformin hydrochloride oral antidiabetic medication for type 2 diabetes management.",
        "price": 310.00,
        "discount_percent": 5.00,
        "category_id": uuid.UUID("a3333333-3333-3333-3333-333333333333"),
        "requires_prescription": True,
        "stocks": {
            "11111111-1111-1111-1111-111111111111": 60,
            "22222222-2222-2222-2222-222222222222": 40,
            "33333333-3333-3333-3333-333333333333": 35,
            "44444444-4444-4444-4444-444444444444": 20,
            "55555555-5555-5555-5555-555555555555": 18,
        },
    },
]

async def seed():
    async with AsyncSessionLocal() as s:
        # 1. Seed Branches
        for b in BRANCHES_DATA:
            existing = (await s.execute(select(Branch).where(Branch.id == b["id"]))).scalar_one_or_none()
            if not existing:
                s.add(Branch(**b))
        await s.flush()
        print("Branches seeded.")

        # 2. Seed Categories
        for cat in CATEGORIES_DATA:
            existing = (await s.execute(select(Category).where(Category.id == cat["id"]))).scalar_one_or_none()
            if not existing:
                s.add(Category(**cat))
        await s.flush()
        print("Categories seeded.")

        # 3. Seed Products & Stock
        for prod_info in PRODUCTS_DATA:
            stocks = prod_info.pop("stocks")
            existing = (await s.execute(select(Product).where(Product.id == prod_info["id"]))).scalar_one_or_none()
            if not existing:
                prod = Product(**prod_info)
                s.add(prod)
                await s.flush()
            else:
                prod = existing

            for branch_id_str, qty in stocks.items():
                b_uuid = uuid.UUID(branch_id_str)
                bs_existing = (await s.execute(
                    select(BranchStock).where(BranchStock.product_id == prod.id, BranchStock.branch_id == b_uuid)
                )).scalar_one_or_none()
                if not bs_existing:
                    s.add(BranchStock(product_id=prod.id, branch_id=b_uuid, stock=qty))
                else:
                    bs_existing.stock = qty
        await s.flush()
        print("Products & BranchStock seeded.")

        # 4. Seed Test Patient
        test_phone = "+923001234567"
        existing_user = (await s.execute(select(User).where(User.phone == test_phone))).scalar_one_or_none()
        if not existing_user:
            user = User(
                full_name="Ali Ahmed (Test Patient)",
                phone=test_phone,
                email="patient.demo@drkarachilab.com",
                hashed_password=hash_password("Password123!"),
                role=UserRole.patient,
                is_active=True,
            )
            s.add(user)
            await s.flush()
            print("Test patient created: phone=+923001234567, password=Password123!")

        await s.commit()
        print("All database seed operations completed successfully!")

if __name__ == "__main__":
    asyncio.run(seed())
