"""Kiểm tra dependency runtime cho UI Thiết lập.

Facade — giữ nguyên import path cũ ``pipeline.core.system_check``:
  probe.py   — probe phần cứng / venv / module import / CUDA / Demucs
  install.py — cài gói AI (pip/uv, torch, VieNeu, OCR GPU, Demucs)
  checks.py  — system_checks() danh sách dependency cho first-run UI
"""
from __future__ import annotations

import sys as _sys
from types import ModuleType as _ModuleType

from . import checks, install, probe

# Re-export mọi tên (kể cả _private mà routes/tests đang dùng) — thứ tự probe →
# checks → install; tên trùng do from-import là cùng object nên ghi đè vô hại.
for _m in (probe, checks, install):
    globals().update(
        {k: v for k, v in vars(_m).items() if not k.startswith("__")}
    )
del _m


class _FacadeModule(_ModuleType):
    """ponytail: tests/routes gán attr lên facade (monkeypatch sc._demucs_check,
    sc._install_log_fn = fn…) nhưng code submodule gọi qua global của chính nó.
    Propagate setattr sang mọi submodule đã có tên đó (kể cả bản from-import).
    monkeypatch.undo setattr lại giá trị cũ → cũng propagate ngược, đối xứng.
    """

    def __setattr__(self, name: str, value) -> None:
        _ModuleType.__setattr__(self, name, value)
        if name.startswith("__"):
            return
        for _mod in (probe, checks, install):
            if name in _mod.__dict__:
                _ModuleType.__setattr__(_mod, name, value)


_sys.modules[__name__].__class__ = _FacadeModule
