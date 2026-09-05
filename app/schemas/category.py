"""
Pydantic schemas for Category endpoints.

NestedCategoryResponse embeds children so a single API call
delivers the full tree for a mega-menu (Medications → sub-categories).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ── Request schemas ────────────────────────────────────────────────────────────
class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    slug: str = Field(..., min_length=2, max_length=255, pattern=r"^[a-z0-9-]+$")
    description: str | None = None
    image_url: str | None = None
    parent_id: uuid.UUID | None = None
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=255)
    slug: str | None = Field(None, pattern=r"^[a-z0-9-]+$")
    description: str | None = None
    image_url: str | None = None
    parent_id: uuid.UUID | None = None
    sort_order: int | None = None


# ── Response schemas ───────────────────────────────────────────────────────────
class CategoryResponse(BaseModel):
    """Flat category — used inside nested responses."""
    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    image_url: str | None
    parent_id: uuid.UUID | None
    sort_order: int

    model_config = {"from_attributes": True}


class NestedCategoryResponse(BaseModel):
    """
    Parent category with its immediate children embedded.
    Used for the mega-menu API — one call delivers the full tree.

    Shape:
      {
        "id": "...",
        "name": "Medications",
        "children": [
          {"id": "...", "name": "Antibiotics", "children": []},
          ...
        ]
      }
    """
    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    image_url: str | None
    sort_order: int
    children: list["NestedCategoryResponse"] = []

    model_config = {"from_attributes": False}


# Enable self-referencing model
NestedCategoryResponse.model_rebuild()
