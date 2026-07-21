"""VieNeu via runtime venv — desktop frozen app (PyInstaller cannot import torch).

Worker process giữ model trên GPU (persistent). Mỗi câu chỉ infer — không load lại
→ util GPU cao. Huỷ job kill worker tree.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_CUDA_READY: bool | None = None

# Worker: 1 dòng JSON request → 1 dòng JSON response; model load 1 lần
_WORKER_SCRIPT = r"""
import json, os, sys, traceback
from pathlib import Path

# Tắt progress bar / log lộn stdout (phá JSON + UTF-8)
os.environ["TQDM_DISABLE"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

def _out(obj):
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()

def _register():
    try:
        from transformers import AutoConfig, AutoModel
        from vieneu._v3_turbo_engine.configuration_v3_turbo import VieNeuV3TurboConfig
        from vieneu._v3_turbo_engine.modeling_v3_turbo import VieNeuV3TurboForTTS
        AutoConfig.register("vieneu_v3", VieNeuV3TurboConfig)
        try:
            AutoModel.register(VieNeuV3TurboConfig, VieNeuV3TurboForTTS)
        except Exception:
            pass
    except Exception:
        pass

def main():
    backend = "onnx"
    device = "cpu"
    client = None
    clone_loaded = set()
    for raw in sys.stdin.buffer:
        try:
            line = raw.decode("utf-8", errors="replace").strip()
        except Exception:
            continue
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as e:
            _out({"ok": False, "error": f"bad json: {e}"})
            continue
        op = msg.get("op") or "synth"
        try:
            if op == "init":
                backend = msg.get("backend") or "pytorch"
                device = msg.get("device") or "cuda"
                _register()
                from vieneu import Vieneu
                client = Vieneu(mode="v3turbo", backend=backend, device=device)
                _out({"ok": True, "backend": str(getattr(client, "backend", backend)), "device": device})
                continue
            if op == "ping":
                _out({"ok": True, "ready": client is not None})
                continue
            if op == "shutdown":
                _out({"ok": True})
                break
            if client is None:
                _out({"ok": False, "error": "not inited"})
                continue
            text = msg.get("text") or "."
            voice = msg.get("voice") or ""
            out_wav = Path(msg.get("out_wav") or "")
            style = msg.get("style") or "tu_nhien"
            clone_ref = msg.get("clone_ref")
            if clone_ref and voice and voice not in clone_loaded:
                client.add_voice(voice, clone_ref, denoise=False, save=False)
                clone_loaded.add(voice)
            out_wav.parent.mkdir(parents=True, exist_ok=True)
            audio = client.infer(text, voice=voice, style=style)
            client.save(audio, str(out_wav))
            _out({"ok": True, "backend": str(getattr(client, "backend", backend))})
        except Exception:
            _out({"ok": False, "error": traceback.format_exc()[-800:]})

if __name__ == "__main__":
    main()
"""

_pool_lock = threading.Lock()
# backend|device -> list of idle workers
_idle: dict[str, list["_Worker"]] = {}
_all_workers: list["_Worker"] = []


class _Worker:
    def __init__(self, py: Path, backend: str, device: str) -> None:
        self.backend = backend
        self.device = device
        self.key = f"{backend}|{device}"
        self._lock = threading.Lock()
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["TQDM_DISABLE"] = "1"
        env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        env["TRANSFORMERS_VERBOSITY"] = "error"
        kw: dict = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,  # progress/warning không phá JSON
            "bufsize": 0,
            "env": env,
        }
        if sys.platform == "win32":
            kw["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.proc = subprocess.Popen([str(py), "-u", "-c", _WORKER_SCRIPT], **kw)
        # register for cancel-kill
        try:
            from pipeline.core.jobs import register_process, current_job_id

            register_process(current_job_id(), self.proc)
        except Exception:
            pass
        init = self._rpc({"op": "init", "backend": backend, "device": device}, timeout=300)
        if not init.get("ok"):
            self.close()
            raise RuntimeError(init.get("error") or "VieNeu worker init failed")

    def _rpc(self, msg: dict, *, timeout: float = 600) -> dict:
        if self.proc.poll() is not None:
            return {"ok": False, "error": "worker dead"}
        assert self.proc.stdin and self.proc.stdout
        payload = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            try:
                self.proc.stdin.write(payload)
                self.proc.stdin.flush()
            except OSError as e:
                return {"ok": False, "error": str(e)}
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    from pipeline.core.jobs import (
                        Cancelled,
                        current_job_id,
                        is_cancelled,
                        kill_process_tree,
                    )

                    if is_cancelled(current_job_id()):
                        kill_process_tree(self.proc)
                        raise Cancelled()
                except Exception as e:
                    if e.__class__.__name__ == "Cancelled":
                        raise
                out = self.proc.stdout.readline()
                if not out:
                    return {"ok": False, "error": "worker EOF"}
                try:
                    text = out.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    return json.loads(text)
                except json.JSONDecodeError:
                    continue
            return {"ok": False, "error": "worker timeout"}

    def synth(
        self,
        *,
        text: str,
        voice: str,
        out_wav: Path,
        style: str,
        clone_ref: str | None,
    ) -> None:
        res = self._rpc(
            {
                "op": "synth",
                "text": text,
                "voice": voice,
                "out_wav": str(out_wav),
                "style": style,
                "clone_ref": clone_ref,
            },
            timeout=600,
        )
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or "VieNeu synth failed")

    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self) -> None:
        try:
            if self.proc.poll() is None and self.proc.stdin:
                self.proc.stdin.write(
                    (json.dumps({"op": "shutdown"}) + "\n").encode("utf-8")
                )
                self.proc.stdin.flush()
        except Exception:
            pass
        try:
            from pipeline.core.jobs import (
                current_job_id,
                kill_process_tree,
                unregister_process,
            )

            if self.proc.poll() is None:
                kill_process_tree(self.proc)
            unregister_process(current_job_id(), self.proc)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def _acquire(backend: str, device: str) -> _Worker:
    key = f"{backend}|{device}"
    with _pool_lock:
        bucket = _idle.setdefault(key, [])
        while bucket:
            w = bucket.pop()
            if w.alive():
                return w
            try:
                w.close()
            except Exception:
                pass
            if w in _all_workers:
                _all_workers.remove(w)
        w = _Worker(runtime_python(), backend, device)
        _all_workers.append(w)
        return w


def _release(w: _Worker) -> None:
    if not w.alive():
        with _pool_lock:
            if w in _all_workers:
                _all_workers.remove(w)
        return
    with _pool_lock:
        _idle.setdefault(w.key, []).append(w)


def shutdown_all_workers() -> None:
    """Gọi khi cancel job / app exit — giải phóng VRAM."""
    with _pool_lock:
        ws = list(_all_workers)
        _all_workers.clear()
        _idle.clear()
    for w in ws:
        try:
            w.close()
        except Exception:
            pass


def runtime_python() -> Path:
    if getattr(sys, "frozen", False):
        home = Path(os.environ["VIDEO_CLONE_HOME"])
        py = home / ".venv-runtime" / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        if py.is_file():
            return py
    return Path(sys.executable)


def _run_runtime(code: str, *, timeout: float = 45.0) -> subprocess.CompletedProcess[str]:
    py = runtime_python()
    return subprocess.run(
        [str(py), "-c", code],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
    )


def runtime_torch_cuda_ready(*, refresh: bool = False) -> bool:
    global _CUDA_READY
    if _CUDA_READY is not None and not refresh:
        return _CUDA_READY
    try:
        from pipeline.core.accel import preferred_torch_device

        _CUDA_READY = preferred_torch_device(refresh=refresh) == "cuda"
        return _CUDA_READY
    except Exception:
        pass
    py = runtime_python()
    if not py.is_file():
        _CUDA_READY = False
        return False
    try:
        proc = _run_runtime(
            "import torch; print(1 if torch.cuda.is_available() else 0)",
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        _CUDA_READY = False
        return False
    _CUDA_READY = proc.returncode == 0 and (proc.stdout or "").strip() == "1"
    return _CUDA_READY


def resolve_backend() -> tuple[str, str]:
    from pipeline.core.accel import preferred_vieneu_backend

    return preferred_vieneu_backend()


def probe() -> tuple[bool, str]:
    py = runtime_python()
    if not py.is_file():
        return False, "thiếu Python runtime (.venv-runtime)"
    backend, device = resolve_backend()
    try:
        w = _acquire(backend, device)
        _release(w)
        return True, f"{backend}/{device}"
    except Exception as e:
        return False, str(e)[-200:]


def synthesize(
    *,
    text: str,
    voice: str,
    out_wav: Path,
    style: str = "tu_nhien",
    backend: str = "onnx",
    device: str = "cpu",
    clone_ref: str | None = None,
) -> None:
    py = runtime_python()
    if not py.is_file():
        raise RuntimeError("Thiếu .venv-runtime — vào Thiết lập → Cài gói AI")
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    # Prefer resolved GPU even if caller passed cpu defaults
    try:
        b2, d2 = resolve_backend()
        if backend in ("", "onnx", "cpu") or device in ("", "cpu"):
            backend, device = b2, d2
    except Exception:
        pass
    w = _acquire(backend, device)
    try:
        from pipeline.core.jobs import is_cancelled, current_job_id, Cancelled

        if is_cancelled(current_job_id()):
            raise Cancelled()
        w.synth(
            text=text,
            voice=voice,
            out_wav=out_wav,
            style=style,
            clone_ref=clone_ref,
        )
    except Exception:
        # worker có thể hỏng — đóng, không trả idle
        try:
            w.close()
        except Exception:
            pass
        with _pool_lock:
            if w in _all_workers:
                _all_workers.remove(w)
        raise
    else:
        _release(w)
