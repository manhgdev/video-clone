"""Cover hardsubs + burn translated captions (facade)."""
from __future__ import annotations

from .burn_parts.pipeline import cover_and_burn
from .burn_parts.ass_util import write_ass
# self-check `python -m pipeline` import qua facade này
from .burn_parts.layout_geo import _cover_box_fit

__all__ = ["cover_and_burn", "write_ass", "_cover_box_fit"]
