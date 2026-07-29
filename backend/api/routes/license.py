"""License endpoints available before the main app is unlocked."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pipeline.core.license import activate_license, license_status

router = APIRouter(prefix="/api/license", tags=["license"])


class ActivateIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)


@router.get("/status")
def get_license_status() -> dict:
    return license_status()


@router.post("/activate")
def activate(body: ActivateIn) -> dict:
    try:
        return activate_license(body.key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Máy chủ key không phản hồi: {exc}") from exc
