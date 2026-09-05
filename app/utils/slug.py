"""
Slug generation utility.
Generates clean, URL-safe slugs from title strings.
"""
from __future__ import annotations

import re
import unicodedata


def slugify(value: str) -> str:
    """
    Convert text to ASCII, convert to lowercase, remove non-alphanumerics,
    and convert spaces to hyphens.
    """
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    value = re.sub(r"[-\s]+", "-", value)
    return value
