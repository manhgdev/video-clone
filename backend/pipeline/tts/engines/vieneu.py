"""VieNeu-TTS local engine — verified against vieneu 3.2.3 (V3TurboVieNeuTTS).

Voice ids:
  vn:<voice_name>          built-in preset (e.g. vn:Phạm Tuyên)
  vn:clone:<id>            user clone

Lazy-load: model only constructed on first synthesize/clone.
Preset list can be read from package assets without loading ONNX graphs.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from ..schemas import PREFIX_VIENEU
from .. import voice_store
_lock = threading.Lock()
_client: Any = None
_client_err: str | None = None
_load_state = "cold"  # cold | loading | ready | error
_reference_lock = threading.Lock()
_reference_cache: dict[str, tuple[int, int, dict[str, Any]]] = {}


def _torch_cuda_ready() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _nvidia_present() -> bool:
    """GPU vật lý có (không cần torch) — để báo thiếu PyTorch CUDA."""
    try:
        import shutil
        import subprocess

        if not shutil.which("nvidia-smi"):
            return False
        r = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        return r.returncode == 0 and bool((r.stdout or "").strip())
    except Exception:
        return False


def _resolve_backend() -> tuple[str, str]:
    """(backend, device) — auto: CUDA+torch → pytorch/cuda, không thì ONNX/CPU.

    CapCut / ElevenLabs chạy cloud — chỉ VieNeu local mới dùng GPU máy.
    """
    env = (os.environ.get("VIENEU_BACKEND") or "auto").strip().lower()
    if env in ("onnx", "cpu"):
        return "onnx", "cpu"
    if env == "pytorch":
        return "pytorch", ("cuda" if _torch_cuda_ready() else "cpu")
    # auto
    if _torch_cuda_ready():
        return "pytorch", "cuda"
    return "onnx", "cpu"


def available() -> bool:
    if os.environ.get("VIENEU_DISABLED", "").strip().lower() in ("1", "true", "yes"):
        return False
    try:
        import importlib.util

        return importlib.util.find_spec("vieneu") is not None
    except Exception:
        return False


def package_version() -> str:
    try:
        from importlib.metadata import version

        return version("vieneu")
    except Exception:
        return ""


def _assets_voices_path() -> Path | None:
    try:
        import vieneu

        root = Path(vieneu.__file__).resolve().parent
        p = root / "assets" / "voices_v3_turbo.json"
        return p if p.is_file() else None
    except Exception:
        return None


def list_preset_from_assets() -> list[dict[str, str]]:
    """Preset metadata from SDK assets without loading the neural model."""
    path = _assets_voices_path()
    if not path:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[dict[str, str]] = []
    for name, v in (data.get("presets") or {}).items():
        meta = v or {}
        description = str(meta.get("description") or "")
        # ponytail: SDK only embeds accent in its human description; keep this
        # exact-token parser until the SDK exposes a dedicated accent field.
        accent = next(
            (part for part in description.split(" · ") if part in {"Bắc", "Trung", "Nam"}),
            "",
        )
        out.append(
            {
                "id": str(name),
                "name": str(name),
                "description": description,
                "gender": str(meta.get("gender") or ""),
                "style": str(meta.get("style") or ""),
                "accent": accent,
                "language": "vi-VN",
            }
        )
    return out


def get_client() -> Any:
    """Lazy singleton — only when synth/clone needs the model."""
    global _client, _client_err, _load_state
    if not available():
        raise RuntimeError(
            "Chưa cài VieNeu-TTS. Trong backend/.venv chạy: pip install vieneu onnxruntime soundfile soxr sea-g2p perth"
        )
    with _lock:
        if _client is not None:
            return _client
        if _client_err and _load_state == "error":
            # allow one retry after error
            pass
        _load_state = "loading"
        try:
            # Prefetch torch cuDNN before any other CUDA package binds the DLL name
            try:
                from ...core.cuda_dll import prefer_torch_cudnn

                prefer_torch_cudnn()
            except Exception:
                pass
            from vieneu import Vieneu

            backend, device = _resolve_backend()
            kwargs: dict[str, Any] = {
                "mode": "v3turbo",
                "backend": backend,
                "device": device,
            }
            precision = (os.environ.get("VIENEU_PRECISION") or "int8").strip().lower()
            if precision in ("int8", "fp32"):
                kwargs["precision"] = precision
            voice_store.ensure_vieneu_dirs()
            # Prefer our voices.json for cloned voices if SDK supports path
            voices_path = voice_store.VOICES_JSON
            _client = Vieneu(**kwargs)
            # Re-enroll clones from disk
            for item in voice_store.load_cloned():
                ref = item.get("ref")
                name = item.get("name") or item.get("id")
                if not ref or not name:
                    continue
                path = voice_store.VIENEU_ROOT / str(ref)
                if not path.is_file():
                    continue
                try:
                    _client.add_voice(str(name), str(path), denoise=False, save=False)
                except Exception:
                    pass
            try:
                if voices_path.is_file():
                    # SDK save_voices path — optional reload
                    pass
            except Exception:
                pass
            _client_err = None
            _load_state = "ready"
            return _client
        except Exception as e:
            _client = None
            _client_err = str(e)
            _load_state = "error"
            raise RuntimeError(f"Không khởi tạo được VieNeu: {e}") from e


def status() -> dict[str, Any]:
    installed = available()
    presets = list_preset_from_assets() if installed else []
    out: dict[str, Any] = {
        "id": "vieneu",
        "name": "VieNeu Local",
        "local": True,
        "installed": installed,
        "ready": installed and _load_state in ("ready", "cold"),
        "loaded": _load_state == "ready",
        "loadState": _load_state,
        "device": "—",
        "model": "VieNeu-TTS-v3-Turbo",
        "version": package_version(),
        "message": "",
        "presetCount": len(presets),
        "installHint": (
            "pip install vieneu onnxruntime soundfile soxr sea-g2p perth && "
            "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124 && "
            "pip install transformers"
        ),
        "cloneRequiresPytorch": True,
    }
    if not installed:
        out["ready"] = False
        out["message"] = "Chưa cài — " + out["installHint"]
        return out
    if _load_state == "error" and _client_err:
        out["ready"] = False
        out["message"] = f"Lỗi load: {_client_err[:180]}"
        return out
    if _load_state == "ready" and _client is not None:
        be = str(getattr(_client, "backend", "onnx") or "onnx").lower()
        if be == "pytorch" and _torch_cuda_ready():
            try:
                import torch

                name = torch.cuda.get_device_name(0)
                out["device"] = f"CUDA · {name}"
            except Exception:
                out["device"] = "CUDA"
        elif be == "pytorch":
            out["device"] = "PyTorch/CPU"
        else:
            out["device"] = "ONNX/CPU"
        out["message"] = "Sẵn sàng (model đã nạp)"
    elif _load_state == "loading":
        out["message"] = "Đang nạp model…"
    else:
        if _torch_cuda_ready():
            out["device"] = "CUDA (lazy)"
            out["message"] = "Đã cài — lần tạo giọng đầu sẽ nạp PyTorch/CUDA"
        elif _nvidia_present():
            out["device"] = "ONNX/CPU"
            out["message"] = (
                "Có GPU nhưng chưa PyTorch CUDA — đang chạy CPU. "
                "Cài: pip install torch torchaudio --index-url "
                "https://download.pytorch.org/whl/cu124 && pip install transformers"
            )
        else:
            out["device"] = "ONNX/CPU (lazy)"
            out["message"] = "Đã cài — model nạp khi tạo giọng lần đầu (CPU)"
    return out


def list_voices(lang: str | None = None) -> list[dict[str, Any]]:
    _ = lang
    if not available():
        return []
    out: list[dict[str, Any]] = []
    for item in voice_store.load_reference_voices():
        voice_id = str(item["id"])
        ref_path = voice_store.reference_path(item)
        out.append(
            {
                **item,
                "id": voice_id,
                "name": str(item.get("name") or voice_id),
                "engine": "zmai",
                "type": "zmAI",
                "available": ref_path.is_file(),
                "tags": voice_store.normalize_voice_tags(item.get("tags")),
                "previewUrl": f"/api/tts/voices/{voice_id}/preview" if ref_path.is_file() else None,
            }
        )
    # The live client also contains user clones, which are appended separately below.
    presets = list_preset_from_assets()
    for preset in presets:
        voice_id = preset["id"]
        out.append(
            {
                **preset,
                "id": f"{PREFIX_VIENEU}{voice_id}",
                "name": f"VieNeu · {preset['name']}",
                "engine": "vieneu",
                "type": "preset",
            }
        )
    for item in voice_store.load_cloned():
        cid = str(item.get("id") or "")
        name = str(item.get("name") or cid)
        if not cid:
            continue
        out.append(
            {
                "id": f"{PREFIX_VIENEU}clone:{cid}",
                "name": f"VieNeu · Clone · {name}",
                "engine": "clone",
                "type": "clone",
                "tags": voice_store.normalize_voice_tags(item.get("tags")),
                "previewUrl": f"/api/tts/voices/{PREFIX_VIENEU}clone:{cid}/preview",
            }
        )
    return out


def preview_path(voice: str) -> Path | None:
    """Return an existing reference WAV for preview; presets have no source audio."""
    parsed = parse_voice(voice)
    if not parsed:
        return None
    kind, voice_id = parsed
    if kind == "reference":
        item = voice_store.get_reference_voice(voice_id)
        path = voice_store.reference_path(item or {})
    elif kind == "clone":
        item = next((x for x in voice_store.load_cloned() if x.get("id") == voice_id), None)
        if not item:
            return None
        path = voice_store.VIENEU_ROOT / str(item.get("ref") or "")
    else:
        return None
    return path if path.is_file() else None


def parse_voice(voice: str) -> tuple[str, str] | None:
    if not voice:
        return None
    direct = voice_store.get_reference_voice(str(voice))
    if direct:
        return "reference", str(direct["id"])
    if not str(voice).startswith(PREFIX_VIENEU):
        return None
    rest = str(voice)[len(PREFIX_VIENEU) :]
    if rest.startswith("clone:"):
        return "clone", rest[6:].strip()
    return "preset", rest.strip()


def reference_cache_token(voice: str) -> str:
    """Fingerprint reference content for encoded/audio-preview cache invalidation."""
    parsed = parse_voice(voice)
    if not parsed or parsed[0] != "reference":
        return ""
    item = voice_store.get_reference_voice(parsed[1])
    path = voice_store.reference_path(item or {})
    try:
        stat = path.stat()
        return f"{parsed[1]}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        return f"{parsed[1]}:missing"


def _encoded_reference(client: Any, voice_id: str) -> dict[str, Any]:
    item = voice_store.get_reference_voice(voice_id)
    if not item:
        raise RuntimeError(f"Không tìm thấy cấu hình giọng zmAI '{voice_id}'.")
    path = voice_store.reference_path(item)
    if not path.is_file():
        raise RuntimeError(
            f"Thiếu file reference cho giọng {item.get('name') or voice_id}: {path}"
        )
    stat = path.stat()
    with _reference_lock:
        cached = _reference_cache.get(voice_id)
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return cached[2]
        try:
            speaker_emb, ref_codes = client.encode_reference(path, denoise=False)
        except Exception as e:
            raise RuntimeError(
                f"Không encode được reference của giọng {item.get('name') or voice_id} "
                f"({path}): {e}"
            ) from e
        encoded = {
            "speaker_emb": speaker_emb,
            "codes": ref_codes,
            "style": "tu_nhien",
        }
        _reference_cache[voice_id] = (stat.st_mtime_ns, stat.st_size, encoded)
        return encoded


def synthesize(
    text: str,
    voice: str,
    out_wav: Path,
    *,
    style: str = "tu_nhien",
) -> None:
    parsed = parse_voice(voice)
    if not parsed:
        raise ValueError(f"Không phải giọng VieNeu: {voice}")
    kind, name = parsed
    client = get_client()
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    text = (text or ".").strip() or "."
    if style not in ("tu_nhien", "tin_tuc", "doc_truyen"):
        style = "tu_nhien"

    if kind == "reference":
        audio = client.infer(
            text,
            voice=_encoded_reference(client, name),
            style=style,
        )
    elif kind == "clone":
        entry = next((x for x in voice_store.load_cloned() if x.get("id") == name), None)
        voice_arg = (entry.get("name") if entry else None) or name
        ref = entry.get("ref") if entry else None
        ref_path = voice_store.VIENEU_ROOT / str(ref) if ref else None
        if ref_path and ref_path.is_file():
            try:
                audio = client.infer(
                    text,
                    ref_audio=str(ref_path),
                    denoise=False,
                    style=style,
                )
            except Exception:
                audio = client.infer(text, voice=str(voice_arg), style=style)
        else:
            audio = client.infer(text, voice=str(voice_arg), style=style)
    else:
        audio = client.infer(text, voice=name, style=style)

    try:
        client.save(audio, str(out_wav))
    except Exception:
        import numpy as np

        arr = np.asarray(audio, dtype=np.float32).reshape(-1)
        _write_wav_pcm16(out_wav, arr, 48000)


def _write_wav_pcm16(path: Path, samples: Any, sr: int) -> None:
    import wave

    import numpy as np

    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    x = np.clip(x, -1.0, 1.0)
    pcm = (x * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def clone_voice(
    name: str,
    ref_path: Path,
    *,
    denoise: bool = True,
    transcript: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Register cloned voice. PyTorch path preferred for denoise; ONNX may fail."""
    _ = transcript  # reserved for future ref_text APIs
    voice_store.ensure_vieneu_dirs()
    display = name.strip() or "clone"
    existing = {str(x.get("id") or "") for x in voice_store.load_cloned()}
    safe = voice_store.make_clone_id(display, existing)
    dest = voice_store.CLONED_DIR / f"{safe}.wav"
    if ref_path.resolve() != dest.resolve():
        import shutil
        import subprocess

        if ref_path.suffix.lower() == ".wav":
            shutil.copy2(ref_path, dest)
        else:
            subprocess.check_call(
                ["ffmpeg", "-y", "-i", str(ref_path), "-ac", "1", "-ar", "48000", str(dest)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    client = get_client()
    try:
        client.add_voice(display, str(dest), denoise=bool(denoise), save=False)
    except Exception as e1:
        try:
            client.add_voice(display, str(dest), denoise=False, save=False)
        except Exception as e2:
            raise RuntimeError(
                "Clone giọng cần engine PyTorch (GPU). "
                "Built-in voices vẫn dùng ONNX/CPU. "
                f"Chi tiết: {e2 or e1}"
            ) from e2
    # Không ghi SDK presets vào voices.json — sẽ xóa hết danh sách clone.
    try:
        client.save_voices(str(voice_store.SDK_VOICES_JSON))
    except Exception:
        pass
    clean_tags = voice_store.normalize_voice_tags(tags, strict=True)
    voice_store.add_cloned(safe, display, f"cloned/{safe}.wav", tags=clean_tags)
    return {
        "id": f"{PREFIX_VIENEU}clone:{safe}",
        "name": f"VieNeu · Clone · {display}",
        "tags": clean_tags,
    }


def warm() -> str:
    try:
        get_client()
        return str(status().get("device") or "ready")
    except Exception as e:
        return f"err:{e}"


def reset_client() -> None:
    """Bỏ model đã nạp — dùng sau khi cài PyTorch CUDA để load lại GPU."""
    global _client, _client_err, _load_state
    with _lock:
        _client = None
        _client_err = None
        _load_state = "cold"
    with _reference_lock:
        _reference_cache.clear()
