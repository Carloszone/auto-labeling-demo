"""Consistent API errors returned by the demo service."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """An expected API error with a stable machine-readable code."""

    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
