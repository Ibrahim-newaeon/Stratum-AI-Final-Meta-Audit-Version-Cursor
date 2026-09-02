# =============================================================================
# Stratum AI - Standard API Response Schemas
# =============================================================================
"""
Standard response envelope schemas.

Re-exports the canonical generic response wrappers defined in
``app.base_schemas`` so endpoints can import them from a stable path:

    from app.schemas.response import APIResponse, PaginatedResponse
"""

from app.base_schemas import APIResponse, PaginatedResponse

__all__ = [
    "APIResponse",
    "PaginatedResponse",
]
