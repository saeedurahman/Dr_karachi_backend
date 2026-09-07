"""
seed_engagement.py — Seed 5 health blog articles, verified completed records, and reviews.

Usage (from backend/ directory):
    python seed_engagement.py

Idempotent: re-running skips existing slugs or records.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(__file__))

from app.config import settings
from app.database_base import Base
from app.models.appointment import Appointment, AppointmentStatus
from app.models.blog import BlogPost
from app.models.branch import Branch
from app.models.doctor import Doctor
from app.models.lab_booking import CollectionType, LabBooking, LabBookingStatus
from app.models.lab_test import LabTest
from app.models.review import Review, ReviewTargetType
from app.models.user import User, UserRole
from app.utils.security import hash_password

BLOG_ARTICLES = [
    {
        "title": "Managing Type 2 Diabetes in Pakistan: Diet, Monitoring & HbA1c",
        "slug": "managing-type-2-diabetes-pakistan",
        "excerpt": "A practical guide to balancing traditional South Asian cuisine, regular blood glucose tracking, and quarterly HbA1c screening.",
        "category": "Diabetes",
        "is_featured": True,
        "meta_title": "Managing Type 2 Diabetes in Pakistan | Dr. Karachi Health Blog",
        "meta_description": "Practical clinical guidance for Pakistani patients managing diabetes through diet, exercise, and routine HbA1c monitoring.",
        "content": """## The Growing Challenge of Diabetes in Pakistan

Diabetes has reached unprecedented levels across urban centers in Pakistan, driven by genetic predisposition and rapid dietary shifts toward refined carbohydrates and sedentary lifestyles. However, with disciplined monitoring and targeted dietary adjustments, individuals can lead healthy, active lives while avoiding microvascular complications.

### 1. Dietary Adjustments Without Sacrificing Culture

Managing blood glucose does not require abandoning traditional meals. Small, sustainable modifications yield significant long-term glycemic control:

- **Switch to Whole Grains**: Replace refined white flour (*maida*) with stone-ground whole wheat (*chakki atta*) or barley.
- **Portion Control with Lentils & Pulses**: Daal, chickpeas, and kidney beans provide essential plant protein and soluble fiber that slows carbohydrate absorption.
- **Cooking Oils**: Minimize hydrogenated vegetable ghee. Transition to cold-pressed mustard oil, olive oil, or moderate portions of clarified butter (*desi ghee*).

### 2. The Critical Role of Routine HbA1c Testing

While daily finger-prick fasting glucose provides an immediate snapshot, the **Glycated Hemoglobin (HbA1c) test** reflects average blood glucose over the preceding 90 days.

| Test Window | Target Range | Clinical Interpretation |
|---|---|---|
| Normal | < 5.7% | Optimal metabolic function |
| Pre-diabetes | 5.7% – 6.4% | Lifestyle intervention window |
| Controlled Diabetes | 6.5% – 7.0% | Excellent therapeutic target |
| Elevated Risk | > 8.0% | Urgent physician medication review |

### 3. Early Warning Symptoms to Discuss with Your Doctor

If you experience recurrent lethargy, increased thirst (*polydipsia*), frequent nighttime urination, or tingling sensations in the feet (*peripheral neuropathy*), schedule an evaluation with an endocrinologist promptly.
""",
    },
    {
        "title": "Essential Preventive Blood Tests Everyone Over 30 Should Take",
        "slug": "essential-preventive-blood-tests-over-30",
        "excerpt": "Why routine annual screening detects metabolic disorders, lipid imbalances, and organ strain years before physical symptoms appear.",
        "category": "Lab Tests",
        "is_featured": True,
        "meta_title": "Annual Preventive Lab Tests Over 30 | Dr. Karachi Lab",
        "meta_description": "Comprehensive guide to routine annual blood screenings for adults over 30 to detect health conditions early.",
        "content": """## Why Preventive Diagnostics Matter

Modern medicine increasingly emphasizes preventive health over reactive disease treatment. Many chronic conditions—including hypertension, atherosclerosis, fatty liver disease, and renal strain—develop silently over several years with zero noticeable symptoms.

An annual blood screening panel provides your physician with an objective roadmap of your internal health.

### The 4 Foundation Panels

#### 1. Complete Blood Count (CBC)
Analyzes 14 blood components including hemoglobin, red cell distribution width, and leukocyte differentials. Screens for subtle nutritional anemia, bone marrow health, and chronic occult infections.

#### 2. Comprehensive Lipid Profile
Measures total cholesterol, LDL (bad cholesterol), HDL (protective cholesterol), and triglycerides. Essential for assessing cardiovascular risk and plaque vulnerability.

#### 3. Liver Function Test (LFT) & Kidney Profile (RFT)
- **LFT**: Detects elevated ALT and AST enzymes signaling fatty liver or toxic medication effects.
- **RFT (Creatinine & Urea)**: Evaluates glomerular filtration efficiency to detect early kidney changes.

#### 4. Fasting Blood Glucose & HbA1c
Screens for insulin resistance years before overt diabetes symptoms emerge.

### Frequency and Preparation

Healthy adults aged 30 to 45 with no chronic complaints should perform this baseline battery once every 12 months. Ensure a strict 10 to 12-hour overnight water-only fast prior to blood sample collection.
""",
    },
    {
        "title": "Heart Health & Cholesterol: Understanding Your Lipid Profile",
        "slug": "heart-health-understanding-lipid-profile",
        "excerpt": "Demystifying HDL, LDL, and triglycerides: what your lab numbers really mean for your long-term cardiovascular longevity.",
        "category": "Cardiology",
        "is_featured": True,
        "meta_title": "Understanding Your Lipid Profile Test | Dr. Karachi",
        "meta_description": "Learn how to read your cholesterol test results, distinguish good vs bad cholesterol, and take proactive steps for heart wellness.",
        "content": """## Understanding Blood Lipids

Cardiovascular disease remains the leading cause of premature mortality across South Asia. Fortunately, comprehensive lipid profiling provides a direct window into arterial health.

### Demystifying the Key Markers

- **Total Cholesterol**: The aggregate quantity of cholesterol circulating in your bloodstream.
- **Low-Density Lipoprotein (LDL)**: Known as "bad" cholesterol. Elevated LDL particles penetrate the endothelium of coronary arteries, creating atherosclerotic plaques.
- **High-Density Lipoprotein (HDL)**: Known as "good" cholesterol. HDL acts as a vascular scavenger, carrying excess cholesterol back to the liver for clearance.
- **Triglycerides**: The chemical storage form of excess caloric intake. Strongly linked to insulin resistance and acute pancreatitis when severely elevated.

### Recommended Target Ranges

- **Total Cholesterol**: Under 200 mg/dL
- **LDL Cholesterol**: Under 100 mg/dL (under 70 mg/dL for high-risk cardiac patients)
- **HDL Cholesterol**: Greater than 40 mg/dL (men) or 50 mg/dL (women)
- **Triglycerides**: Under 150 mg/dL

### Actionable Steps for Lipid Management

1. **Cardiovascular Exercise**: 30 minutes of brisk walking 5 days a week raises protective HDL levels.
2. **Eliminate Industrial Trans Fats**: Avoid repeatedly reheated commercial frying oils commonly found in street snacks.
3. **Omega-3 Fatty Acids**: Incorporate walnuts, chia seeds, and grilled fish to lower circulating triglycerides.
""",
    },
    {
        "title": "Vitamin D Deficiency in Karachi: Causes, Symptoms, and Safe Supplementation",
        "slug": "vitamin-d-deficiency-karachi-symptoms-care",
        "excerpt": "Despite abundant sunshine, over 70% of urban residents suffer from low Vitamin D levels. Here is how to diagnose and correct it safely.",
        "category": "Wellness",
        "is_featured": False,
        "meta_title": "Vitamin D Deficiency in Karachi | Dr. Karachi Health Blog",
        "meta_description": "Understand why Vitamin D deficiency is prevalent in sunny Pakistan and how clinical diagnostics can guide safe replenishment.",
        "content": """## The Sunlight Paradox

Despite Karachi enjoying more than 300 sunny days per year, clinical diagnostic audits indicate that more than 70% of urban adults have clinically insufficient 25-hydroxy Vitamin D levels (< 20 ng/mL).

### Why Is Deficiency So Pervasive?

- **Indoor Lifestyles**: Office work, air-conditioned commuting, and residential architectural density prevent direct midday skin exposure.
- **Melanin Protection**: Darker skin pigmentation contains higher melanin density, which acts as a natural sunscreen and reduces cutaneous synthesis of Vitamin D3.
- **Air Quality & Pollution**: Atmospheric particulate matter (*PM 2.5*) filters UVB wavelengths necessary for photolytic conversion in the epidermis.

### Common Signs of Low Vitamin D

- Unexplained muscle aches and generalized bone discomfort.
- Persistent daytime fatigue and brain fog.
- Depressed mood or seasonal affective patterns.
- Frequent respiratory infections and impaired immune recovery.

### The Correct Clinical Approach

Before taking massive oral megadoses (e.g. 200,000 IU ampoules), patients should confirm their baseline with a **25-OH Vitamin D Total blood test**. Unmonitored supplementation carries risk of hypercalcemia, kidney stones, and soft tissue calcification. Consult your physician for a weight-adjusted therapeutic protocol.
""",
    },
    {
        "title": "Seasonal Allergies and Smog Protection: A Clinical Guide",
        "slug": "seasonal-allergies-smog-protection-clinical-guide",
        "excerpt": "Effective clinical strategies to protect your lungs and sinuses against particulate pollution, dry coastal winds, and seasonal pollen.",
        "category": "Respiratory",
        "is_featured": False,
        "meta_title": "Smog & Allergy Protection Guide | Dr. Karachi Health Blog",
        "meta_description": "Clinical advice for managing respiratory allergies, asthma triggers, and urban smog protection in Sindh.",
        "content": """## Air Quality and Upper Respiratory Health

The winter and early spring transition in Karachi and surrounding regions is characterized by lower humidity, stagnant air inversions, and elevated particulate pollution. These environmental factors exacerbate allergic rhinitis, sinusitis, and bronchial asthma.

### Key Protective Measures

1. **High-Efficiency Filtration Masks**: Standard cloth masks provide minimal defense against sub-micron particles. When the Air Quality Index (AQI) exceeds 150, use certified N95 or KN95 respirators outdoors.
2. **Isotonic Saline Sinus Rinses**: Performing a gentle saline nasal wash after arriving home mechanically removes trapped pollen and particulate matter before it triggers an inflammatory cascade.
3. **Hydration & Vocal Rest**: Drink 2.5 to 3 liters of clean water daily to maintain the mucosal lining's protective ciliary action.
4. **Air Purifiers for Sleep**: If suffering from nighttime wheezing or dry coughing, a HEPA air purifier in the bedroom can reduce overnight allergen exposure by up to 90%.

### When to Seek Immediate Medical Evaluation

Consult a pulmonologist or visit your nearest Dr. Karachi clinic if you experience persistent shortness of breath, audible wheezing that does not respond to inhalers, chest tightness, or productive discolored phlegm lasting longer than 5 days.
""",
    },
]


async def run_seed():
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    print(f"Connecting to database: {db_url.split('@')[-1]}")
    engine = create_async_engine(db_url, echo=False)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_maker() as session:
        # 1. Fetch author / admin user
        admin_res = await session.execute(
            select(User).where(User.role.in_([UserRole.super_admin, UserRole.doctor])).limit(1)
        )
        author = admin_res.scalar_one_or_none()
        if not author:
            any_user_res = await session.execute(select(User).limit(1))
            author = any_user_res.scalar_one_or_none()
        author_id = author.id if author else None

        # 2. Seed Blog Posts
        print("\n--- Seeding Health Blog Articles ---")
        now = datetime.now(UTC)
        for b_data in BLOG_ARTICLES:
            existing = await session.execute(
                select(BlogPost).where(BlogPost.slug == b_data["slug"], BlogPost.deleted_at.is_(None))
            )
            if existing.scalar_one_or_none():
                print(f"  [Skip] Blog post '{b_data['slug']}' already exists.")
                continue

            post = BlogPost(
                id=uuid.uuid4(),
                title=b_data["title"],
                slug=b_data["slug"],
                content=b_data["content"],
                excerpt=b_data["excerpt"],
                author_id=author_id,
                is_published=True,
                is_featured=b_data["is_featured"],
                published_at=now - timedelta(days=2),
                view_count=42,
                meta_title=b_data["meta_title"],
                meta_description=b_data["meta_description"],
            )
            session.add(post)
            print(f"  [Created] Blog post: {b_data['title']} (slug: {b_data['slug']})")

        await session.flush()

        # 3. Look up primary test patient
        patient_res = await session.execute(
            select(User).where(User.phone == "+923001234567")
        )
        patient = patient_res.scalar_one_or_none()

        # Look up doctor
        doctor_res = await session.execute(
            select(Doctor).where(Doctor.deleted_at.is_(None)).limit(1)
        )
        doctor = doctor_res.scalar_one_or_none()

        # Look up lab test
        lab_test_res = await session.execute(
            select(LabTest).where(LabTest.deleted_at.is_(None)).limit(1)
        )
        lab_test = lab_test_res.scalar_one_or_none()

        # Look up branch
        branch_res = await session.execute(
            select(Branch).where(Branch.is_active.is_(True)).limit(1)
        )
        branch = branch_res.scalar_one_or_none()

        if patient and doctor and branch:
            print("\n--- Ensuring Verified Patient Completed Appointment ---")
            appt_check = await session.execute(
                select(Appointment).where(
                    Appointment.patient_id == patient.id,
                    Appointment.doctor_id == doctor.id,
                    Appointment.status == AppointmentStatus.completed,
                ).limit(1)
            )
            if not appt_check.scalar_one_or_none():
                completed_appt = Appointment(
                    id=uuid.uuid4(),
                    patient_id=patient.id,
                    doctor_id=doctor.id,
                    branch_id=branch.id,
                    slot_datetime=datetime.now(UTC) - timedelta(days=5),
                    status=AppointmentStatus.completed,
                    notes="Follow-up consultation completed successfully.",
                )
                session.add(completed_appt)
                print(f"  [Created] Completed appointment for patient {patient.phone} with Dr. {doctor.id}")
            else:
                print(f"  [Exists] Completed appointment exists for patient {patient.phone}")

        if patient and lab_test and branch:
            print("\n--- Ensuring Verified Patient Completed Lab Booking ---")
            booking_check = await session.execute(
                select(LabBooking).where(
                    LabBooking.patient_id == patient.id,
                    LabBooking.test_id == lab_test.id,
                    LabBooking.status == LabBookingStatus.completed,
                ).limit(1)
            )
            if not booking_check.scalar_one_or_none():
                completed_booking = LabBooking(
                    id=uuid.uuid4(),
                    patient_id=patient.id,
                    test_id=lab_test.id,
                    branch_id=branch.id,
                    collection_type=CollectionType.clinic_visit,
                    preferred_date=date.today() - timedelta(days=3),
                    time_slot="morning",
                    status=LabBookingStatus.completed,
                    total_price=lab_test.price,
                    notes="Routine checkup completed.",
                )
                session.add(completed_booking)
                print(f"  [Created] Completed lab booking for test '{lab_test.name}'")
            else:
                print(f"  [Exists] Completed lab booking exists for test '{lab_test.name}'")

        # 4. Create secondary patient for approved sample reviews
        print("\n--- Seeding Community Patient & Approved Reviews ---")
        review_user_res = await session.execute(
            select(User).where(User.phone == "+923009998877")
        )
        reviewer = review_user_res.scalar_one_or_none()
        if not reviewer:
            reviewer = User(
                id=uuid.uuid4(),
                full_name="Zainab Malik",
                phone="+923009998877",
                password_hash=hash_password("Password123!"),
                role=UserRole.patient,
                is_active=True,
            )
            session.add(reviewer)
            await session.flush()
            print("  [Created] Reviewer user: Zainab Malik (+923009998877)")

        # Create completed records for reviewer so their review is valid
        if doctor and branch:
            rev_appt = await session.execute(
                select(Appointment).where(
                    Appointment.patient_id == reviewer.id,
                    Appointment.doctor_id == doctor.id,
                    Appointment.status == AppointmentStatus.completed,
                ).limit(1)
            )
            if not rev_appt.scalar_one_or_none():
                session.add(
                    Appointment(
                        id=uuid.uuid4(),
                        patient_id=reviewer.id,
                        doctor_id=doctor.id,
                        branch_id=branch.id,
                        slot_datetime=datetime.now(UTC) - timedelta(days=10),
                        status=AppointmentStatus.completed,
                        notes="Consultation completed.",
                    )
                )

            # Doctor Review
            doc_review_check = await session.execute(
                select(Review).where(
                    Review.user_id == reviewer.id,
                    Review.target_type == ReviewTargetType.doctor,
                    Review.target_id == doctor.id,
                )
            )
            if not doc_review_check.scalar_one_or_none():
                doc_review = Review(
                    id=uuid.uuid4(),
                    user_id=reviewer.id,
                    target_type=ReviewTargetType.doctor,
                    target_id=doctor.id,
                    rating=5,
                    comment="Dr. Ahmed was very attentive, explained my diagnostic test results clearly, and provided reassuring guidance on medication. Highly recommended!",
                    is_approved=True,
                    created_at=now - timedelta(days=7),
                    updated_at=now - timedelta(days=7),
                )
                session.add(doc_review)
                print(f"  [Created] Approved review for doctor {doctor.id}")

        if lab_test and branch:
            rev_lab = await session.execute(
                select(LabBooking).where(
                    LabBooking.patient_id == reviewer.id,
                    LabBooking.test_id == lab_test.id,
                    LabBooking.status == LabBookingStatus.completed,
                ).limit(1)
            )
            if not rev_lab.scalar_one_or_none():
                session.add(
                    LabBooking(
                        id=uuid.uuid4(),
                        patient_id=reviewer.id,
                        test_id=lab_test.id,
                        branch_id=branch.id,
                        collection_type=CollectionType.home_sampling,
                        preferred_date=date.today() - timedelta(days=8),
                        time_slot="morning",
                        collection_address="DHA Phase 5, Karachi",
                        status=LabBookingStatus.completed,
                        total_price=lab_test.price,
                    )
                )

            # Lab Test Review
            lab_review_check = await session.execute(
                select(Review).where(
                    Review.user_id == reviewer.id,
                    Review.target_type == ReviewTargetType.service,
                    Review.target_id == lab_test.id,
                )
            )
            if not lab_review_check.scalar_one_or_none():
                lab_review = Review(
                    id=uuid.uuid4(),
                    user_id=reviewer.id,
                    target_type=ReviewTargetType.service,
                    target_id=lab_test.id,
                    rating=5,
                    comment="The phlebotomist arrived right on time for home collection. Punctual, hygienic, and results were ready online within 12 hours.",
                    is_approved=True,
                    created_at=now - timedelta(days=6),
                    updated_at=now - timedelta(days=6),
                )
                session.add(lab_review)
                print(f"  [Created] Approved review for lab test '{lab_test.name}'")

        await session.commit()
        print("\nAll engagement seed operations completed successfully!")


if __name__ == "__main__":
    asyncio.run(run_seed())
