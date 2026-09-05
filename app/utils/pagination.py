"""Pagination utilities for list endpoints."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")


@dataclass
class PaginationParams:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def pagination_params(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
) -> PaginationParams:
    """FastAPI dependency — inject as: params: PaginationParams = Depends(pagination_params)"""
    return PaginationParams(page=page, page_size=page_size)


class PagedResponse(BaseModel, Generic[T]):
    """Standard paginated list response envelope."""

    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int

    @classmethod
    def create(cls, items: list[T], total: int, params: PaginationParams) -> "PagedResponse[T]":
        pages = max(1, -(-total // params.page_size))  # ceiling division
        return cls(
            items=items,
            total=total,
            page=params.page,
            page_size=params.page_size,
            pages=pages,
        )
