"""Routes of the checks before submit (milestone 2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter


def make_checks_router(st: Any, prefix: str = "/api") -> APIRouter:
    return APIRouter()
