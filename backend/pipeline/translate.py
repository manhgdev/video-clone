"""Machine translation facade."""
from __future__ import annotations

from .mt.api import translate_segments, _with_google_fallback
from .mt.text import _clean_burn_text

__all__ = ["translate_segments", "_clean_burn_text", "_with_google_fallback"]
