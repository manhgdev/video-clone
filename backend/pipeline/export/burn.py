"""Cover hardsubs + burn translated captions (facade)."""
from __future__ import annotations

from .burn_parts.pipeline import cover_and_burn
from .burn_parts.ass_util import write_ass

__all__ = ["cover_and_burn", "write_ass"]
