"""
Products router â€” /api/v1/pharmacy/products

Key behaviors:
- ?branch_id=  filter returns stock_at_branch from branch_stock
- All responses include original_price, discounted_price, discount_percent
  (frontend never calculates â€” just renders)
- Soft-delete: product stays in DB, removed from listings
- Stock management: staff upserts into branch_stock (not product.stock)
"""
from __future__ import annotations

import codecs
import csv
import uuid
from io import StringIO
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.dependencies import DBSession, require_roles
from app.models.category import Category
from app.models.product import BranchStock, Product
from app.models.user import UserRole
from app.schemas.product import (
    BranchStockUpsert,
    ProductCreate,
    ProductResponse,
    ProductUpdate,
)
from app.utils.pagination import PagedResponse, PaginationParams, pagination_params
from app.utils.storage import generate_upload_path, get_product_images_storage

router = APIRouter(prefix="/products", tags=["Pharmacy â€” Products"])

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


def _product_response(product: Product, stock: int | None = None) -> ProductResponse:
    return ProductResponse.from_orm_with_branch(product, branch_stock=stock)


@router.get(
    "/",
    response_model=PagedResponse[ProductResponse],
    summary="List products (filter by branch, category, search)",
)
async def list_products(
    db: DBSession,
    params: PaginationParams = Depends(pagination_params),
    branch_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    search: str | None = None,
    in_stock_only: bool = False,
    include_inactive: bool = False,
):
    """
    ?branch_id    â†’ returns stock_at_branch field, filters out-of-stock if in_stock_only=true
    ?category_id  â†’ filter by category
    ?search       â†’ name/sku ilike search
    ?in_stock_only â†’ only return products with stock > 0 at selected branch
    """
    query = select(Product).where(Product.deleted_at.is_(None))
    if not include_inactive:
        query = query.where(Product.is_active == True)

    if category_id:
        query = query.where(Product.category_id == category_id)
    if search:
        query = query.where(
            Product.name.ilike(f"%{search}%") | Product.sku.ilike(f"%{search}%")
        )

    # If branch filter: join branch_stock for availability
    if branch_id and in_stock_only:
        query = query.join(
            BranchStock,
            (BranchStock.product_id == Product.id) & (BranchStock.branch_id == branch_id),
        ).where(BranchStock.stock > 0)

    # Count total
    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar_one()

    # Paginated results
    result = await db.execute(
        query.order_by(Product.name).offset(params.offset).limit(params.page_size)
    )
    products = result.scalars().all()

    # Build responses â€” fetch stock per-product if branch_id provided
    responses: list[ProductResponse] = []
    for p in products:
        stock_qty: int | None = None
        if branch_id:
            bs_result = await db.execute(
                select(BranchStock.stock).where(
                    BranchStock.product_id == p.id,
                    BranchStock.branch_id == branch_id,
                )
            )
            stock_qty = bs_result.scalar_one_or_none() or 0
        responses.append(_product_response(p, stock_qty))

    return PagedResponse.create(responses, total, params)


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Get product detail",
)
async def get_product(
    product_id: uuid.UUID,
    db: DBSession,
    branch_id: uuid.UUID | None = None,
):
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    stock_qty: int | None = None
    if branch_id:
        bs_result = await db.execute(
            select(BranchStock.stock).where(
                BranchStock.product_id == product_id,
                BranchStock.branch_id == branch_id,
            )
        )
        stock_qty = bs_result.scalar_one_or_none() or 0

    return _product_response(product, stock_qty)


@router.post(
    "/",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="[Staff/Admin] Create product",
    dependencies=[require_roles(UserRole.super_admin, UserRole.pharmacy_staff)],
)
async def create_product(body: ProductCreate, db: DBSession):
    # SKU uniqueness check (partial index gives DB guarantee, this gives a nice error)
    existing = await db.execute(
        select(Product).where(Product.sku == body.sku, Product.deleted_at.is_(None))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"SKU '{body.sku}' already exists.")

    product = Product(
        name=body.name,
        sku=body.sku,
        description=body.description,
        price=float(body.price),
        discount_percent=float(body.discount_percent),
        category_id=body.category_id,
        requires_prescription=body.requires_prescription,
    )
    db.add(product)
    await db.flush()
    return _product_response(product)


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    summary="[Staff/Admin] Update product",
    dependencies=[require_roles(UserRole.super_admin, UserRole.pharmacy_staff)],
)
async def update_product(product_id: uuid.UUID, body: ProductUpdate, db: DBSession):
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    data = body.model_dump(exclude_none=True)
    if "price" in data:
        data["price"] = float(data["price"])
    if "discount_percent" in data:
        data["discount_percent"] = float(data["discount_percent"])

    for field, value in data.items():
        setattr(product, field, value)
    return _product_response(product)


@router.post(
    "/{product_id}/image",
    response_model=ProductResponse,
    summary="[Staff/Admin] Upload product image (jpg/jpeg/png/webp, max 5MB)",
    dependencies=[require_roles(UserRole.super_admin, UserRole.pharmacy_staff)],
)
async def upload_product_image(product_id: uuid.UUID, db: DBSession, file: UploadFile = File(...)):
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    filename = file.filename or "image"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS or file.content_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file format. Allowed: JPG, JPEG, PNG, WEBP.",
        )

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(content) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of 5MB ({len(content)} bytes).",
        )
    await file.seek(0)

    storage = get_product_images_storage()
    dest_path = generate_upload_path("products", filename)
    saved_path = await storage.upload(file, dest_path)
    product.image_url = storage.get_url(saved_path)
    await db.flush()
    return _product_response(product)


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="[Admin] Soft-delete product",
    dependencies=[require_roles(UserRole.super_admin)],
)
async def delete_product(product_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    product.soft_delete()


@router.put(
    "/{product_id}/stock",
    response_model=dict,
    summary="[Staff/Admin] Set stock for a product at a specific branch",
    dependencies=[require_roles(UserRole.super_admin, UserRole.pharmacy_staff)],
)
async def upsert_branch_stock(product_id: uuid.UUID, body: BranchStockUpsert, db: DBSession):
    """
    Upserts (create or update) the branch_stock record.
    This is the only way to modify stock â€” no direct product.stock field exists.
    """
    result = await db.execute(
        select(BranchStock).where(
            BranchStock.product_id == product_id,
            BranchStock.branch_id == body.branch_id,
        )
    )
    bs = result.scalar_one_or_none()
    if bs:
        bs.stock = body.stock
    else:
        bs = BranchStock(
            product_id=product_id,
            branch_id=body.branch_id,
            stock=body.stock,
        )
        db.add(bs)
    await db.flush()
    return {"product_id": product_id, "branch_id": body.branch_id, "stock": bs.stock}


# ─── 1. Template Download ────────────────────────────────────────────────────────

@router.get(
    "/import/template",
    summary="[Staff/Admin] Download product import template CSV",
    dependencies=[require_roles(UserRole.super_admin, UserRole.pharmacy_staff)],
)
async def download_import_template():
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "name", "sku", "description", "price", 
        "discount_percent", "category_name", "requires_prescription"
    ])
    writer.writerow([
        "Example Panadol", "SKU-PAN-001", "Pain relief tablets", "120.00", 
        "0", "Pain Relief", "no"
    ])
    
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=product_import_template.csv"}
    )

# ─── Helper: Parse and Validate CSV File ──────────────────────────────────────────

async def parse_and_validate_import(file: UploadFile, db: DBSession, commit: bool = False) -> dict[str, Any]:
    if not file.filename.endswith(('.csv', '.xlsx')):
        raise HTTPException(status_code=400, detail="Only .csv files are supported for now.")
    
    # Read the file content
    try:
        content = await file.read()
        decoded_content = codecs.decode(content, 'utf-8')
        csv_reader = csv.DictReader(StringIO(decoded_content))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid CSV format.")
    finally:
        await file.seek(0)

    rows = []
    summary = {"create": 0, "skip": 0, "skipped_at_commit": 0, "uncategorized": 0, "error": 0}
    
    # Pre-fetch existing SKUs and categories for fast lookup
    skus_result = await db.execute(select(Product.sku))
    existing_skus = {sku for (sku,) in skus_result.all()}
    
    cats_result = await db.execute(select(Category))
    existing_categories = {c.name.lower(): c.id for c in cats_result.scalars().all()}
    
    uncategorized_id = existing_categories.get("uncategorized")

    for i, row in enumerate(csv_reader):
        row_num = i + 2 # Header is row 1
        sku = row.get("sku", "").strip().upper()
        name = row.get("name", "").strip()
        price_str = row.get("price", "0").strip()
        discount_str = row.get("discount_percent", "0").strip()
        category_name = row.get("category_name", "").strip()
        req_rx_str = row.get("requires_prescription", "no").strip().lower()

        # Hard validations
        if not sku or not name:
            rows.append({"row_num": row_num, "sku": sku or "MISSING", "status": "error", "message": "SKU and Name are required."})
            summary["error"] += 1
            continue
            
        try:
            price = float(price_str)
            if price <= 0:
                raise ValueError
        except ValueError:
            rows.append({"row_num": row_num, "sku": sku, "status": "error", "message": "Price must be a positive number."})
            summary["error"] += 1
            continue

        try:
            discount_percent = float(discount_str)
            if not (0 <= discount_percent <= 100):
                raise ValueError
        except ValueError:
            rows.append({"row_num": row_num, "sku": sku, "status": "error", "message": "Discount must be between 0 and 100."})
            summary["error"] += 1
            continue

        requires_prescription = req_rx_str in ['yes', 'true', '1', 'y']

        # Conflict check
        if sku in existing_skus:
            # If committing, it means a conflict happened after preview, or it was already known.
            if commit:
                summary["skipped_at_commit"] += 1
            else:
                summary["skip"] += 1
            rows.append({"row_num": row_num, "sku": sku, "status": "skip", "message": "SKU already exists."})
            continue

        # Category mapping
        cat_id = existing_categories.get(category_name.lower())
        status = "create"
        msg = "Ready to create"

        if not cat_id:
            status = "uncategorized"
            msg = f"Assigned to Uncategorized (Original: '{category_name}')"
            summary["uncategorized"] += 1
            
            if commit:
                # Create Uncategorized if it doesn't exist yet
                if not uncategorized_id:
                    new_cat = Category(name="Uncategorized", slug="uncategorized")
                    db.add(new_cat)
                    await db.flush()
                    uncategorized_id = new_cat.id
                    existing_categories["uncategorized"] = uncategorized_id
                cat_id = uncategorized_id
        else:
            summary["create"] += 1

        rows.append({
            "row_num": row_num,
            "sku": sku,
            "name": name,
            "status": status,
            "message": msg,
            "original_category": category_name
        })

        if commit:
            description_text = row.get("description", "").strip()
            new_product = Product(
                sku=sku,
                name=name,
                description=description_text if description_text else None,
                price=price,
                discount_percent=discount_percent,
                category_id=cat_id,
                requires_prescription=requires_prescription,
                is_active=True
            )
            db.add(new_product)
            # Add to local set to catch duplicate SKUs within the same CSV file
            existing_skus.add(sku)

    return {"rows": rows, "summary": summary}

# ─── 2. Preview Endpoint ─────────────────────────────────────────────────────────

@router.post(
    "/import/preview",
    summary="[Staff/Admin] Preview products import",
    dependencies=[require_roles(UserRole.super_admin, UserRole.pharmacy_staff)],
)
async def preview_products_import(
    db: DBSession,
    file: UploadFile = File(...),
):
    return await parse_and_validate_import(file, db, commit=False)

# ─── 3. Commit Endpoint ──────────────────────────────────────────────────────────

@router.post(
    "/import/commit",
    summary="[Staff/Admin] Commit products import",
    dependencies=[require_roles(UserRole.super_admin, UserRole.pharmacy_staff)],
)
async def commit_products_import(
    db: DBSession,
    file: UploadFile = File(...),
):
    try:
        # DBSession manages the transaction but if an error occurs we should rollback explicitly
        result = await parse_and_validate_import(file, db, commit=True)
        await db.commit()
        return result
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
