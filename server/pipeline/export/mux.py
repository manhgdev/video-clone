"""Mix TTS onto video with BGM ducking."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ..core.jobs import run_cmd
from ..core.media import _has_audio_stream, ffprobe_duration, h264_encoder_args
from ..core.project import ensure_layout, out_final, set_status


def _num(v: Any, default: float) -> float:
    """JSON null / missing → default (seg.get('x', d) vẫn trả None khi key=null)."""
    if v is None:
        return float(default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def set_stem_progress(
    project_id: str | None,
    progress: int,
    message: str = "",
    *,
    running: bool = True,
) -> None:
    """Tiến độ tách no_vocals (preview) — file riêng, không đè status xuất."""
    if not project_id:
        return
    root = ensure_layout(project_id)
    path = root / "cache" / "stem_progress.json"
    data = {
        "progress": max(0, min(100, int(progress))),
        "message": str(message or ""),
        "running": bool(running),
        "ts": time.time(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def read_stem_progress(project_id: str) -> dict[str, Any]:
    path = ensure_layout(project_id) / "cache" / "stem_progress.json"
    if not path.is_file():
        return {"progress": 0, "message": "", "running": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"progress": 0, "message": "", "running": False}
        return {
            "progress": max(0, min(100, int(data.get("progress") or 0))),
            "message": str(data.get("message") or ""),
            "running": bool(data.get("running")),
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {"progress": 0, "message": "", "running": False}


def _wav_rms(path: Path) -> float:
    """RMS thô pcm_s16le (0..1) — chẩn đoán stem Demucs gần im."""
    import struct

    try:
        raw = subprocess.check_output(
            [
                "ffmpeg", "-v", "error", "-i", str(path),
                "-ac", "1", "-ar", "8000", "-f", "s16le", "-t", "120", "-",
            ],
            timeout=60,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return 0.0
    if len(raw) < 4:
        return 0.0
    n = len(raw) // 2
    # lấy mẫu thưa để nhanh
    step = max(1, n // 40000)
    acc = 0.0
    count = 0
    for i in range(0, n, step):
        (sample,) = struct.unpack_from("<h", raw, i * 2)
        acc += float(sample) * float(sample)
        count += 1
    if count <= 0:
        return 0.0
    return (acc / count) ** 0.5 / 32768.0


def _nvidia_smi_ok() -> bool:
    try:
        r = subprocess.run(["nvidia-smi"], capture_output=True, timeout=12)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine().lower() in ("arm64", "aarch64")


def _demucs_backend_wanted() -> str:
    """cuda | mlx | cpu — backend tách lời tối ưu theo máy."""
    if _nvidia_smi_ok():
        return "cuda"
    if _apple_silicon():
        return "mlx"  # demucs-mlx (Metal) — torch MPS thiếu op complex
    return "cpu"


def _torch_device(exe: Path) -> str:
    """cuda | mps | cpu — probe torch trong venv (không gồm mlx)."""
    if not exe.is_file():
        return "cpu"
    try:
        r = subprocess.run(
            [
                str(exe),
                "-c",
                (
                    "import torch\n"
                    "if torch.cuda.is_available():\n"
                    " print('cuda')\n"
                    "elif getattr(torch.backends, 'mps', None) and torch.backends.mps.is_available():\n"
                    " print('mps')\n"
                    "else:\n"
                    " print('cpu')\n"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        out = (r.stdout or "").strip().lower()
        if r.returncode == 0 and out in ("cuda", "mps", "cpu"):
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    return "cpu"


def _has_pkg(exe: Path, import_stmt: str, *, timeout: float = 90) -> bool:
    if not exe.is_file():
        return False
    try:
        r = subprocess.run(
            [str(exe), "-c", import_stmt],
            capture_output=True,
            timeout=timeout,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _demucs_accel(exe: Path) -> str:
    """Backend sẵn có trong venv: mlx | cuda | mps | cpu."""
    if _apple_silicon() and _has_pkg(exe, "import demucs_mlx"):
        return "mlx"
    if _has_pkg(exe, "import demucs, soundfile"):
        return _torch_device(exe)
    return "cpu"


def _demucs_jobs() -> int:
    # Demucs -j: song song preprocess; Apple Silicon nhiều lõi → tới 6
    n = os.cpu_count() or 2
    cap = 6 if _apple_silicon() else 4
    return max(1, min(cap, max(2, n // 2)))


# cu124: NVIDIA. Mac arm64: pip mặc định (MPS trong torch; tách thì dùng demucs-mlx).
_TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu124"
_TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"


def _pip_install_torch(py: Path, *, accel: str, project_id: str | None) -> None:
    """accel: cuda | cpu | mac (PyPI macOS arm64 có Metal trong torch)."""
    pip = [str(py), "-m", "pip"]
    if accel == "cuda":
        label = "CUDA"
        set_stem_progress(project_id, 10, f"Cài PyTorch {label} (có thể vài phút)…")
        subprocess.run(
            pip + ["uninstall", "-y", "torch", "torchaudio", "torchvision"],
            capture_output=True,
            timeout=300,
        )
        cmd = pip + [
            "install",
            "--upgrade",
            "torch",
            "torchaudio",
            "--index-url",
            _TORCH_CUDA_INDEX,
        ]
    elif accel == "mac":
        label = "macOS (Metal)"
        set_stem_progress(project_id, 10, f"Cài PyTorch {label}…")
        cmd = pip + ["install", "--upgrade", "torch", "torchaudio"]
    else:
        label = "CPU"
        set_stem_progress(project_id, 10, f"Cài PyTorch {label}…")
        if sys.platform == "darwin":
            cmd = pip + ["install", "--upgrade", "torch", "torchaudio"]
        else:
            cmd = pip + [
                "install",
                "--upgrade",
                "torch",
                "torchaudio",
                "--index-url",
                _TORCH_CPU_INDEX,
            ]
    r_torch = subprocess.run(cmd, capture_output=True, text=True, timeout=2400)
    if r_torch.returncode != 0:
        raise RuntimeError(
            f"Không cài được PyTorch {label} cho Demucs.\n"
            + ((r_torch.stderr or r_torch.stdout or "")[-800:])
        )


def _pip_install_demucs_mlx(py: Path, project_id: str | None) -> None:
    pip = [str(py), "-m", "pip"]
    set_stem_progress(project_id, 12, "Cài demucs-mlx (Apple GPU / Metal)…")
    r = subprocess.run(
        pip + ["install", "--upgrade", "demucs-mlx", "soundfile"],
        capture_output=True,
        text=True,
        timeout=1200,
    )
    if r.returncode != 0:
        raise RuntimeError(
            "Không cài được demucs-mlx.\n" + ((r.stderr or r.stdout or "")[-800:])
        )


def _pip_install_demucs_torch(py: Path, project_id: str | None) -> None:
    pip = [str(py), "-m", "pip"]
    set_stem_progress(project_id, 14, "Cài Demucs…")
    r = subprocess.run(
        pip + ["install", "--upgrade", "demucs", "soundfile"],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if r.returncode != 0:
        raise RuntimeError(
            "Không cài được Demucs.\n" + ((r.stderr or r.stdout or "")[-800:])
        )


def _demucs_root_candidates() -> list[Path]:
    """Ưu tiên VIDEO_CLONE_HOME (app), rồi server/, rồi LocalAppData."""
    roots: list[Path] = []
    home = os.environ.get("VIDEO_CLONE_HOME", "").strip()
    if home:
        roots.append(Path(home))
    server = Path(__file__).resolve().parents[2]
    roots.append(server)
    if sys.platform == "win32":
        la = Path(os.environ.get("LOCALAPPDATA", "") or "") / "VideoClone"
        if str(la):
            roots.append(la)
    elif sys.platform == "darwin":
        roots.append(Path.home() / "Library" / "Application Support" / "VideoClone")
    else:
        roots.append(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "VideoClone")
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = str(r.resolve()) if r.exists() else str(r)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _demucs_py_in(root: Path) -> Path:
    return root / ".venv-demucs" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )


def _demucs_install_root() -> Path:
    """Nơi tạo/cài venv Demucs: app home khi frozen, server/ khi dev."""
    home = os.environ.get("VIDEO_CLONE_HOME", "").strip()
    if home or getattr(sys, "frozen", False):
        return Path(home or _demucs_root_candidates()[0])
    return Path(__file__).resolve().parents[2]


def _demucs_python(project_id: str | None = None, *, report: bool = True) -> str:
    """Python có demucs: Apple Silicon → demucs-mlx; NVIDIA → torch CUDA; khác → CPU."""
    wanted = _demucs_backend_wanted()

    def _ready(exe: Path) -> bool:
        if wanted == "mlx":
            return _has_pkg(exe, "import demucs_mlx, soundfile")
        return _has_pkg(exe, "import demucs, soundfile")

    def _ensure(exe: Path) -> None:
        if wanted == "mlx":
            if _ready(exe):
                return
            if report and project_id:
                set_status(
                    project_id,
                    step="export",
                    progress=62,
                    message="Đang cài demucs-mlx (Apple Silicon GPU)…",
                    running=True,
                )
            set_stem_progress(project_id, 8, "Nâng cấp Demucs → Apple Metal (MLX)…")
            _pip_install_demucs_mlx(exe, project_id)
            return
        if wanted == "cuda":
            if _ready(exe) and _torch_device(exe) == "cuda":
                return
            if report and project_id:
                set_status(
                    project_id,
                    step="export",
                    progress=62,
                    message="Đang cài PyTorch CUDA cho tách lời…",
                    running=True,
                )
            set_stem_progress(project_id, 8, "Nâng cấp PyTorch → CUDA…")
            try:
                _pip_install_torch(exe, accel="cuda", project_id=project_id)
            except RuntimeError:
                set_stem_progress(project_id, 10, "CUDA fail — fallback CPU…")
                _pip_install_torch(exe, accel="cpu", project_id=project_id)
            if _torch_device(exe) != "cuda":
                set_stem_progress(project_id, 10, "Torch CUDA không nhận GPU — fallback CPU…")
                _pip_install_torch(exe, accel="cpu", project_id=project_id)
            if not _has_pkg(exe, "import demucs, soundfile"):
                _pip_install_demucs_torch(exe, project_id)
            return
        # CPU (hoặc Intel Mac)
        if _ready(exe):
            return
        _pip_install_torch(
            exe,
            accel="mac" if sys.platform == "darwin" else "cpu",
            project_id=project_id,
        )
        _pip_install_demucs_torch(exe, project_id)

    # 1) Dùng venv đã có demucs (app home / server / LocalAppData)
    for root in _demucs_root_candidates():
        cand = _demucs_py_in(root)
        if not _ready(cand):
            continue
        if wanted == "cuda" and _torch_device(cand) != "cuda":
            try:
                _ensure(cand)
            except Exception:
                pass
            if _ready(cand):
                return str(cand)
            continue
        return str(cand)

    if not getattr(sys, "frozen", False):
        cur = Path(sys.executable)
        if _ready(cur) and wanted != "cuda":
            return str(cur)

    # 2) Cài vào root chuẩn (app home khi packaged)
    install_root = _demucs_install_root()
    venv = install_root / ".venv-demucs"
    py = _demucs_py_in(install_root)

    if report and project_id:
        set_status(
            project_id,
            step="export",
            progress=62,
            message="Đang cài Demucs (xóa lời AI) — lần đầu có thể mất vài phút…",
            running=True,
        )
    set_stem_progress(project_id, 4, "Đang cài Demucs / backend GPU (lần đầu)…")
    if not py.is_file():
        if getattr(sys, "frozen", False):
            uv = shutil.which("uv")
            if not uv:
                raise RuntimeError("Bản ứng dụng thiếu uv để cài Demucs")
            subprocess.run(
                [uv, "venv", "--python", "3.12", "--seed", str(venv)],
                check=True,
                capture_output=True,
                timeout=900,
            )
        else:
            subprocess.run(
                [sys.executable, "-m", "venv", str(venv)],
                check=True,
                capture_output=True,
                timeout=180,
            )
    pip = [str(py), "-m", "pip"]
    set_stem_progress(project_id, 6, "Cài pip / wheel…")
    subprocess.run(pip + ["install", "-U", "pip", "wheel"], capture_output=True, timeout=300)
    _ensure(py)
    if not _ready(py):
        raise RuntimeError(
            f"Đã cài Demucs nhưng import vẫn lỗi — kiểm tra {venv}"
        )
    accel = _demucs_accel(py)
    set_stem_progress(project_id, 16, f"Đã sẵn sàng Demucs ({accel})")
    return str(py)


_PCT_RE = re.compile(r"(\d{1,3})\s*%")

# demucs-mlx API → no_vocals.wav (drums+bass+other). Torch MPS thiếu complex → không dùng.
_MLX_SEPARATE_PY = r"""
import sys
from pathlib import Path
import numpy as np
import soundfile as sf

src = Path(sys.argv[1])
out_root = Path(sys.argv[2])
from demucs_mlx import Separator

sep = Separator(model="htdemucs", shifts=1, overlap=0.25)
_origin, stems = sep.separate_audio_file(str(src))
sr = int(getattr(sep, "sample_rate", None) or getattr(sep, "samplerate", None) or 44100)
track = out_root / "htdemucs" / src.stem
track.mkdir(parents=True, exist_ok=True)

def to_sf(audio):
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim == 2 and a.shape[0] <= 8 and a.shape[0] < a.shape[-1]:
        a = a.T
    return a

for name, audio in stems.items():
    sf.write(str(track / f"{name}.wav"), to_sf(audio), sr)

parts = [to_sf(stems[k]).astype(np.float64) for k in stems if str(k) != "vocals"]
if not parts:
    raise SystemExit("no non-vocal stems")
mix = parts[0]
for p in parts[1:]:
    n = min(mix.shape[0], p.shape[0])
    mix = mix[:n] + p[:n]
peak = float(np.max(np.abs(mix))) if mix.size else 1.0
if peak > 1.0:
    mix = mix / peak
sf.write(str(track / "no_vocals.wav"), mix.astype(np.float32), sr)
print("OK", track)
"""


def _run_demucs_mlx_progress(
    project_id: str,
    python: str,
    source_wav: Path,
    separated: Path,
) -> tuple[int, str]:
    """Apple Silicon: demucs-mlx trên Metal (nhanh hơn torch MPS / CPU)."""
    set_stem_progress(project_id, 18, "Demucs-MLX (Apple GPU) đang tách…")
    separated.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    kw: dict = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    if sys.platform == "win32":
        kw["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    proc = subprocess.Popen(
        [python, "-c", _MLX_SEPARATE_PY, str(source_wav), str(separated)],
        **kw,
    )
    assert proc.stdout is not None
    last_pct = 18
    lock = threading.Lock()
    stop_hb = threading.Event()
    err_chunks: list[str] = []

    def _heartbeat() -> None:
        nonlocal last_pct
        while not stop_hb.wait(2.0):
            with lock:
                if last_pct < 88:
                    last_pct = min(88, last_pct + 2)
                    set_stem_progress(
                        project_id, last_pct, f"Demucs-MLX (Apple GPU)… {last_pct}%"
                    )

    hb = threading.Thread(target=_heartbeat, name="stem-mlx-hb", daemon=True)
    hb.start()
    try:
        for line in proc.stdout:
            err_chunks.append(line)
            if len(err_chunks) > 60:
                err_chunks = err_chunks[-30:]
            if line.strip().startswith("OK"):
                with lock:
                    last_pct = 88
                    set_stem_progress(project_id, 88, "Demucs-MLX xong — ghi stem…")
        code = proc.wait(timeout=3600)
    except Exception:
        proc.kill()
        raise
    finally:
        stop_hb.set()
        hb.join(timeout=1.0)
    return code, "".join(err_chunks)[-800:]


def _run_demucs_progress(
    project_id: str,
    python: str,
    source_wav: Path,
    separated: Path,
) -> tuple[int, str]:
    """Chạy demucs: mlx (Apple) / cuda / mps / cpu."""
    accel = _demucs_accel(Path(python))
    if accel == "mlx":
        return _run_demucs_mlx_progress(project_id, python, source_wav, separated)

    device = accel if accel in ("cuda", "mps", "cpu") else "cpu"
    jobs = _demucs_jobs()
    # CUDA 6GB: segment 6; MPS thử không segment trước
    segment = "6" if device == "cuda" else None
    set_stem_progress(
        project_id,
        18,
        f"Demucs đang tách ({device}, -j {jobs})…",
    )

    def _launch(seg: str | None) -> subprocess.Popen[str]:
        cmd = [
            python,
            "-m",
            "demucs",
            "--two-stems",
            "vocals",
            "--shifts",
            "1",
            "--overlap",
            "0.25",
            "-j",
            str(jobs),
            "--device",
            device,
            "-o",
            str(separated),
        ]
        if seg:
            cmd.extend(["--segment", seg])
        cmd.append(str(source_wav))
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "TQDM_MINITERS": "1"}
        kw: dict = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        if sys.platform == "win32":
            kw["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        return subprocess.Popen(cmd, **kw)

    def _consume(proc: subprocess.Popen[str]) -> tuple[int, str]:
        assert proc.stdout is not None
        last_pct = 18
        lock = threading.Lock()
        stop_hb = threading.Event()
        err_chunks: list[str] = []

        def _heartbeat() -> None:
            nonlocal last_pct
            while not stop_hb.wait(2.5):
                with lock:
                    if last_pct < 88:
                        last_pct = min(88, last_pct + 1)
                        set_stem_progress(
                            project_id, last_pct, f"Demucs ({device})… {last_pct}%"
                        )

        hb = threading.Thread(target=_heartbeat, name="stem-hb", daemon=True)
        hb.start()
        try:
            for line in proc.stdout:
                err_chunks.append(line)
                if len(err_chunks) > 80:
                    err_chunks = err_chunks[-40:]
                m = _PCT_RE.search(line.replace("\r", " "))
                if not m:
                    continue
                raw = max(0, min(100, int(m.group(1))))
                mapped = 18 + int(raw * 0.70)
                with lock:
                    if mapped > last_pct:
                        last_pct = mapped
                        set_stem_progress(
                            project_id, last_pct, f"Demucs ({device})… {last_pct}%"
                        )
            code = proc.wait(timeout=3600)
        except Exception:
            proc.kill()
            raise
        finally:
            stop_hb.set()
            hb.join(timeout=1.0)
        return code, "".join(err_chunks)[-600:]

    proc = _launch(segment)
    code, err_tail = _consume(proc)
    # MPS không hỗ trợ → fallback CPU một lần
    mps_fail = (
        code != 0
        and device == "mps"
        and any(
            s in err_tail.lower()
            for s in ("not implemented", "mps", "complex", "backend")
        )
    )
    if mps_fail:
        set_stem_progress(project_id, 18, "MPS không hỗ trợ model — fallback CPU…")
        shutil.rmtree(separated, ignore_errors=True)
        separated.mkdir(parents=True, exist_ok=True)
        device = "cpu"
        proc2 = _launch(None)
        return _consume(proc2)
    oom = code != 0 and any(
        s in err_tail.lower()
        for s in ("out of memory", "cuda out of memory", "cudnn_status")
    )
    if oom and device == "cuda" and segment != "4":
        set_stem_progress(project_id, 18, "GPU thiếu VRAM — thử segment nhỏ hơn…")
        shutil.rmtree(separated, ignore_errors=True)
        separated.mkdir(parents=True, exist_ok=True)
        proc2 = _launch("4")
        code, err_tail = _consume(proc2)
    return code, err_tail


def separate_no_vocals(
    project_id: str, video: Path, *, report: bool = True
) -> Path:
    """Demucs: bỏ stem vocals, giữ nhạc/SFX.

    Không dùng stereotools làm «xóa lời» — filter đó vẫn để lại lời, cache sai.
    Demucs lỗi → nền im (đúng hơn còn lời).
    report=False: gọi từ preview, không ghi đè status job xuất.
    """
    root = ensure_layout(project_id)
    stat = video.stat()
    # v5: bắt buộc Demucs thật; invalidate cache stereotools (v4 trở xuống)
    key = hashlib.sha1(
        f"{video.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|v5".encode()
    ).hexdigest()[:12]
    cache = root / "cache" / f"no_vocals_{key}.wav"
    if cache.exists() and cache.stat().st_size > 1024:
        set_stem_progress(project_id, 100, "Đã có stem xóa lời", running=False)
        return cache

    set_stem_progress(project_id, 2, "Chuẩn bị tách xóa lời…")
    python = _demucs_python(project_id, report=report)
    work = root / "cache" / f"demucs_{key}"
    work.mkdir(parents=True, exist_ok=True)
    source_wav = work / "source.wav"
    set_stem_progress(project_id, 12, "Trích âm thanh từ video…")
    run_cmd(
        project_id,
        [
            "ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "2", "-ar", "44100",
            str(source_wav),
        ],
    )

    demucs_ok = False
    result: Path | None = None
    separated = work / "separated"
    demucs_err = ""
    try:
        if report:
            set_status(
                project_id,
                step="export",
                progress=66,
                message="Demucs đang xóa lời (giữ nhạc/SFX)…",
                running=True,
            )
        code, demucs_err = _run_demucs_progress(
            project_id, python, source_wav, separated
        )
        if code != 0 and not demucs_err:
            demucs_err = f"exit {code}"
        result = separated / "htdemucs" / source_wav.stem / "no_vocals.wav"
        demucs_ok = result.exists() and result.stat().st_size > 1024
        if not demucs_ok and not demucs_err:
            demucs_err = "không thấy file no_vocals.wav sau Demucs"
    except Exception as e:
        demucs_ok = False
        demucs_err = str(e)[:600]

    if demucs_ok and result is not None:
        set_stem_progress(project_id, 92, "Chỉnh mức âm stem…")
        # Video thoại mono: stem gần im — boost nhẹ, KHÔNG trộn lại gốc.
        src_rms = max(_wav_rms(source_wav), 1e-6)
        stem_rms = _wav_rms(result)
        ratio = stem_rms / src_rms
        if ratio >= 0.12:
            gain = min(2.6, max(1.25, 0.72 / max(ratio, 0.12)))
        elif ratio >= 0.02:
            gain = min(3.5, max(1.5, 0.15 / max(ratio, 0.001)))
        else:
            # Gần như không còn nhạc/SFX — giữ gần im (đúng với clip chỉ lời)
            gain = 1.0
        run_cmd(
            project_id,
            [
                "ffmpeg", "-y", "-i", str(result),
                "-af",
                f"volume={gain:.3f},alimiter=limit=0.95:level=disabled",
                "-c:a", "pcm_s16le", str(cache),
            ],
        )
        set_stem_progress(project_id, 100, "Xong xóa lời", running=False)
    else:
        # Demucs thất bại: nền im — stereotools cũ để lại lời → lệch setting «Xóa lời».
        if report and project_id:
            set_status(
                project_id,
                step="export",
                progress=68,
                message=(
                    "Demucs lỗi — tạm tắt âm gốc (tránh còn lời). "
                    f"Chi tiết: {demucs_err[:180]}"
                    if demucs_err
                    else "Demucs lỗi — tạm tắt âm gốc (tránh còn lời)."
                ),
                running=True,
            )
        set_stem_progress(
            project_id,
            0,
            f"Lỗi tách: {(demucs_err or 'không rõ')[:120]}",
            running=False,
        )
        dur = max(0.1, ffprobe_duration(source_wav) or ffprobe_duration(video) or 1.0)
        run_cmd(
            project_id,
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", f"{dur:.3f}",
                "-c:a", "pcm_s16le", str(cache),
            ],
        )

    shutil.rmtree(work, ignore_errors=True)
    if not cache.exists():
        raise RuntimeError(
            "Không tạo được track xóa lời (Demucs)."
            + (f" {demucs_err}" if demucs_err else "")
        )
    return cache

def _bg_duck_expr(
    segments: list[dict[str, Any]], keep: float = 0.35, duck: float = 0.12
) -> str:
    """ffmpeg volume= expr: duck during speech windows, else keep BGM."""
    ranges: list[tuple[float, float]] = []
    for seg in segments:
        try:
            s, e = float(seg["start"]), float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if e <= s:
            continue
        ranges.append((s, e))
    ranges.sort()
    merged: list[list[float]] = []
    for s, e in ranges:
        if merged and s <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    windows = [f"between(t\\,{s:.3f}\\,{e:.3f})" for s, e in merged]
    if not windows:
        return str(keep)
    return f"if({'+'.join(windows)}\\,{duck}\\,{keep})"


def _source_audio_filter(mode: str) -> str:
    """FFmpeg stem approximation for stereo sources (fast, no model download)."""
    stereo = "aformat=channel_layouts=stereo"
    if mode == "vocals":
        return (
            stereo
            + ",pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1"
            + ",dialoguenhance=enhance=2.0:voice=4.0"
        )
    if mode == "music":
        # Hạ mid (lời thường ở giữa), giữ side (nhạc), bù gain mạnh hơn.
        return stereo + ",stereotools=mlev=0.22:slev=1.25,volume=3.6"
    return "anull"


def _atempo_chain(ratio: float) -> str:
    """atempo chain for speed ratio (output_dur = input_dur / ratio)."""
    parts: list[str] = []
    r = max(0.05, float(ratio))
    while r > 2.0 + 1e-6:
        parts.append("atempo=2.0")
        r /= 2.0
    while r < 0.5 - 1e-6:
        parts.append("atempo=0.5")
        r /= 0.5
    parts.append(f"atempo={r:.4f}")
    return ",".join(parts)


def plan_video_slowdown_factor(
    segments: list[dict[str, Any]],
    root: Path,
    *,
    match: str = "preferVideo",
) -> float:
    """Chỉ tính video_factor (>1 = chậm toàn video). Dùng lúc dub + xuất."""
    _clips, vf = _tts_clip_plan(segments, root, allow_video_slowdown=True, match=match)
    return float(vf)


# preferVideo: chậm cố định 0.80× (setpts 1/0.8) — đủ chỗ TTS dịch dài, không ép đọc
PREFER_VIDEO_SPEED = 0.80
PREFER_VIDEO_FACTOR = 1.0 / PREFER_VIDEO_SPEED  # 1.25


def _tts_clip_plan(
    segments: list[dict[str, Any]],
    root: Path,
    *,
    allow_video_slowdown: bool = True,
    match: str = "preferVideo",
) -> tuple[list[tuple[Path, float, float, float, float]], float]:
    """Trả (clips, video_factor).

    video_factor > 1 = chậm **toàn bộ** video để TTS gần tốc độ tự nhiên.
    clips: (wav, start_sec_scaled, play_sec, tts_speed, volume)

    preferVideo đã bake 0.80×: **cascade** — không atrim giữa câu.
    start_i = max(seg.start, prev_end + gap); speed nhẹ ≤1.25; full audio.
    """
    ordered = sorted(
        [s for s in segments if s],
        key=lambda s: float(s.get("start") or 0),
    )
    gap = 0.03
    # Timeline đã giãn bằng retime_video_segments (videoSpeed) khi TTS dài.
    # Ở đây: full TTS, speed ≈ 1; chỉ atempo nhẹ nếu vẫn tràn.
    baked_prefer = match == "preferVideo" and not allow_video_slowdown
    if match == "preferVideo":
        max_video_factor = PREFER_VIDEO_FACTOR
        soft_tts_speed = 1.06
        hard_tts_cap = 1.15  # sau retime hiếm khi cần; không cắt
        fixed_factor = 1.0 if baked_prefer else (
            PREFER_VIDEO_FACTOR if allow_video_slowdown else 1.0
        )
    elif match == "none":
        max_video_factor = 1.45
        soft_tts_speed = 1.08
        hard_tts_cap = 1.30
        fixed_factor = None
    else:
        max_video_factor = 1.35
        soft_tts_speed = 1.12
        hard_tts_cap = 1.45
        fixed_factor = None

    raw: list[tuple[Path, float, float, float, float, float]] = []
    for i, seg in enumerate(ordered):
        name = seg.get("audioFile") or f"{seg['id']}.wav"
        wav = root / "tts" / name
        if not wav.exists():
            wav = root / "tts" / f"{seg['id']}.wav"
        if not wav.exists():
            continue
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or start)
        ad = float(seg.get("audioDuration") or 0)
        if ad <= 0.05:
            ad = ffprobe_duration(wav) or 0.0
        next_start = None
        for j in range(i + 1, len(ordered)):
            ns = float(ordered[j].get("start") or 0)
            if ns > start + 0.02:
                next_start = ns
                break
        if next_start is not None:
            slot0 = max(0.12, next_start - start - gap)
        else:
            # câu cuối / sau retime: đủ chỗ full TTS
            slot0 = max(0.15, ad + 0.15 if ad > 0.05 else end - start + 0.12)
        raw.append(
            (
                wav,
                start,
                slot0,
                ad,
                max(0.0, min(2.0, _num(seg.get("ttsVolume"), 100) / 100)),
                max(0.75, min(1.5, _num(seg.get("ttsSpeed"), 1))),
            )
        )

    if not raw:
        return [], (fixed_factor if fixed_factor is not None else 1.0)

    video_factor = 1.0
    if fixed_factor is not None:
        video_factor = float(fixed_factor)
    elif allow_video_slowdown:
        needs: list[float] = []
        for _wav, _start, slot0, ad, _volume, manual_speed in raw:
            ad_m = ad / manual_speed
            if ad_m > 0.08 and slot0 > 0.05 and ad_m > slot0 * soft_tts_speed:
                needs.append(ad_m / (slot0 * soft_tts_speed))
        if needs:
            needs.sort()
            idx = min(len(needs) - 1, max(0, int(len(needs) * 0.90) - 1))
            video_factor = needs[idx]
            mid = needs[len(needs) // 2]
            video_factor = max(video_factor, min(mid, max_video_factor))
        video_factor = min(max_video_factor, max(1.0, video_factor))

    # Full TTS: trim = toàn bộ audio sau atempo nhẹ; không atrim theo slot ngắn
    clips: list[tuple[Path, float, float, float, float]] = []
    for wav, start, slot0, ad, volume, manual_speed in raw:
        slot = slot0 * video_factor
        ad_eff = ad / max(manual_speed, 0.05) if ad > 0.05 else slot
        speed = 1.0
        if ad_eff > 0.08 and ad_eff > slot * soft_tts_speed:
            speed = min(hard_tts_cap, ad_eff / max(slot, 0.05))
            speed = max(1.0, speed)
        played = ad_eff / max(speed, 0.05) if ad_eff > 0.05 else slot
        # Ưu tiên đọc hết — trim = full play (slot đã giãn bởi retime)
        trim = max(0.08, played + 0.04)
        clips.append((wav, start * video_factor, trim, speed * manual_speed, volume))
    clips.sort(key=lambda c: c[1])
    out: list[tuple[Path, float, float, float, float]] = []
    for i, (wav, start, trim, sp, volume) in enumerate(clips):
        next_start = clips[i + 1][1] if i + 1 < len(clips) else None
        if next_start is not None and trim > next_start - start - 0.02:
            # retime đã đẩy câu sau — hiếm; chỉ siết nếu vẫn đè
            room = max(0.08, next_start - start - 0.02)
            if trim > room * 1.02:
                # tăng tốc thêm thay vì cắt (giữ full nếu có thể)
                need_sp = sp * (trim / room)
                if need_sp <= hard_tts_cap * 1.05:
                    sp = min(hard_tts_cap, need_sp)
                    trim = room
                else:
                    trim = room
        out.append((wav, start, trim, sp, volume))
    return out, video_factor


def _mix_tts_track(
    project_id: str,
    segments: list[dict[str, Any]],
    root: Path,
    *,
    video_factor: float = 1.0,
    allow_video_slowdown: bool = True,
    match: str = "preferVideo",
) -> Path:
    """Trộn TTS theo timeline đã scale. TTS speed nhẹ; video chậm bù."""
    ordered_plan, plan_vf = _tts_clip_plan(
        segments, root, allow_video_slowdown=allow_video_slowdown, match=match
    )
    # Dùng plan (đã tính factor); video_factor chỉ để cache key khớp mux_dub
    if abs(video_factor - plan_vf) > 0.02 and video_factor > 1.0:
        # re-scale starts/slots if caller forces different factor
        scale = video_factor / max(plan_vf, 1e-6)
        ordered_plan = [
            (w, s * scale, slot * scale, sp, volume)
            for w, s, slot, sp, volume in ordered_plan
        ]
        plan_vf = video_factor

    if not ordered_plan:
        raise RuntimeError("Chưa có audio TTS — chạy Lồng tiếng trước.")

    signature = [
        f"{w.name}@{s:.3f}@{slot:.3f}@{sp:.3f}@{volume:.3f}"
        for w, s, slot, sp, volume in ordered_plan
    ]
    key = hashlib.sha1(
        (f"v11|retime-fit|vf{plan_vf:.3f}|{match}|" + "|".join(signature)).encode()
    ).hexdigest()[:16]
    out = root / "cache" / f"tts_mix_{key}.wav"
    if out.exists():
        return out

    batch_size = 20
    batches: list[Path] = []
    for batch_i, offset in enumerate(range(0, len(ordered_plan), batch_size)):
        batch = ordered_plan[offset : offset + batch_size]
        batch_out = root / "cache" / f"tts_mix_{key}_part{batch_i}.wav"
        inputs: list[str] = []
        filters: list[str] = []
        labels: list[str] = []
        for i, (wav, start_sec, max_sec, speed, volume) in enumerate(batch):
            delay_ms = max(0, int(start_sec * 1000))
            inputs += ["-i", str(wav)]
            parts: list[str] = []
            if speed > 1.03:
                parts.append(_atempo_chain(speed))
            elif speed < 0.97:
                parts.append(_atempo_chain(speed))
            parts.append(f"volume={volume:.3f}")
            # max_sec = full play duration (cascade); pad nhỏ tránh cắt sample cuối
            play_sec = max(0.08, float(max_sec) + 0.05)
            fade = min(0.03, max(0.012, play_sec * 0.02))
            st_fade = max(0.0, play_sec - fade)
            parts.append(f"atrim=0:{play_sec:.3f}")
            parts.append("asetpts=PTS-STARTPTS")
            parts.append(f"afade=t=out:st={st_fade:.3f}:d={fade:.3f}")
            parts.append(f"adelay={delay_ms}|{delay_ms}")
            filters.append(f"[{i}:a]" + ",".join(parts) + f"[a{i}]")
            labels.append(f"[a{i}]")
        filters.append(
            "".join(labels)
            + f"amix=inputs={len(labels)}:duration=longest:normalize=0,"
            f"alimiter=limit=0.95[aout]"
        )
        run_cmd(
            project_id,
            [
                "ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters),
                "-map", "[aout]", "-c:a", "pcm_s16le", str(batch_out),
            ],
        )
        batches.append(batch_out)

    if len(batches) == 1:
        batches[0].replace(out)
    else:
        inputs = [arg for wav in batches for arg in ("-i", str(wav))]
        labels = "".join(f"[{i}:a]" for i in range(len(batches)))
        run_cmd(
            project_id,
            [
                "ffmpeg", "-y", *inputs, "-filter_complex",
                labels
                + f"amix=inputs={len(batches)}:duration=longest:normalize=0,"
                f"alimiter=limit=0.95[aout]",
                "-map", "[aout]", "-c:a", "pcm_s16le", str(out),
            ],
        )
        for wav in batches:
            wav.unlink(missing_ok=True)
    return out


def mux_dub(
    project_id: str,
    video: Path,
    segments: list[dict[str, Any]],
    *,
    original_audio_mode: str = "auto",
    source_audio: Path | None = None,
    original_audio_volume: float = 1.0,
    allow_video_slowdown: bool = True,
    match: str = "preferVideo",
) -> Path:
    """Đặt TTS theo timeline; chậm **toàn video** nếu TTS dài hơn slot."""
    root = ensure_layout(project_id)
    duration = ffprobe_duration(video)
    _clips, video_factor = _tts_clip_plan(
        segments, root, allow_video_slowdown=allow_video_slowdown, match=match
    )
    voice_track = _mix_tts_track(
        project_id,
        segments,
        root,
        video_factor=video_factor,
        allow_video_slowdown=allow_video_slowdown,
        match=match,
    )
    out_dur = duration * video_factor
    vol_mul = max(0.0, min(1.0, float(original_audio_volume)))
    inputs = ["-i", str(video)]
    filters: list[str] = []
    source_audio_index = 0
    next_input_index = 1
    use_preseparated = (
        source_audio is not None
        and source_audio.exists()
        and _has_audio_stream(source_audio)
    )
    if use_preseparated:
        inputs += ["-i", str(source_audio)]
        source_audio_index = 1
        next_input_index = 2
    inputs += ["-i", str(voice_track)]
    filters.append(f"[{next_input_index}:a]anull[voice]")

    # Stem đã xóa lời: nền to, duck nhẹ khi TTS. Audio gốc: duck mạnh hơn.
    # vol_mul (0–1) từ slider UI nhân vào keep/duck.
    if use_preseparated:
        keep, duck = 1.0 * vol_mul, 0.62 * vol_mul
    else:
        keep, duck = 0.42 * vol_mul, 0.14 * vol_mul
    has_bg = (
        vol_mul > 0.001
        and original_audio_mode != "mute"
        and (use_preseparated or _has_audio_stream(video))
    )
    # Duck windows scale theo video_factor
    duck_segs = segments
    if abs(video_factor - 1.0) > 0.001:
        duck_segs = []
        for s in segments:
            ss = dict(s)
            ss["start"] = float(s.get("start") or 0) * video_factor
            ss["end"] = float(s.get("end") or 0) * video_factor
            duck_segs.append(ss)

    if has_bg:
        vol = _bg_duck_expr(duck_segs, keep=keep, duck=duck)
        if use_preseparated:
            source_filter = "anull"
        elif original_audio_mode in ("vocals", "music", "no_vocals"):
            mode = "music" if original_audio_mode == "no_vocals" else original_audio_mode
            source_filter = _source_audio_filter(mode)
        else:
            source_filter = "anull"
        # Chậm BGM cùng nhịp video (atempo < 1)
        bg_tempo = _atempo_chain(1.0 / video_factor) if video_factor > 1.001 else "anull"
        filters.append(
            f"[{source_audio_index}:a]{source_filter},{bg_tempo},"
            f"apad=whole_dur={out_dur:.3f},volume={vol}:eval=frame[bg]"
        )
        filters.append(
            "[bg][voice]amix=inputs=2:duration=longest:dropout_transition=0:"
            "normalize=0[aout]"
        )
        map_audio = "[aout]"
    else:
        map_audio = "[voice]"

    # Video: chậm nhẹ nếu cần (setpts > 1)
    if video_factor > 1.001:
        filters.append(f"[0:v]setpts={video_factor:.4f}*PTS[vout]")
        map_video = "[vout]"
        vcodec = h264_encoder_args(fast=True)
    else:
        map_video = "0:v"
        vcodec = ["-c:v", "copy"]

    out = out_final(project_id)
    fc = ";".join(filters)
    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        fc,
        "-map",
        map_video,
        "-map",
        map_audio,
        *vcodec,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-shortest",
        "-t",
        str(out_dur),
        str(out),
    ]
    run_cmd(project_id, cmd)
    return out


def mux_original_audio(
    project_id: str,
    video: Path,
    mode: str,
    *,
    source_audio: Path | None = None,
    original_audio_volume: float = 1.0,
) -> Path:
    """Xuất video chỉ với track gốc đã lọc, hoặc bỏ hoàn toàn track âm thanh."""
    out = out_final(project_id)
    vol_mul = max(0.0, min(1.0, float(original_audio_volume)))
    use_preseparated = (
        source_audio is not None
        and source_audio.exists()
        and _has_audio_stream(source_audio)
    )
    cmd = ["ffmpeg", "-y", "-i", str(video)]
    if use_preseparated:
        cmd += ["-i", str(source_audio)]
    cmd += ["-map", "0:v", "-c:v", "copy"]
    if mode == "mute" or vol_mul <= 0.001:
        cmd += ["-an"]
    elif use_preseparated:
        # Stem Demucs — volume slider
        if abs(vol_mul - 1.0) > 0.01:
            cmd += [
                "-map", "1:a:0",
                "-af", f"volume={vol_mul:.3f}",
                "-c:a", "aac",
            ]
        else:
            cmd += ["-map", "1:a:0", "-c:a", "aac"]
    elif not _has_audio_stream(video):
        cmd += ["-an"]
    else:
        af = (
            _source_audio_filter("music")
            if mode == "no_vocals"
            else _source_audio_filter(mode)
        )
        if abs(vol_mul - 1.0) > 0.01:
            af = f"{af},volume={vol_mul:.3f}"
        cmd += ["-map", "0:a:0", "-af", af, "-c:a", "aac"]
    cmd += ["-map_metadata", "-1", "-map_chapters", "-1", "-shortest", str(out)]
    run_cmd(project_id, cmd)
    return out


if __name__ == "__main__":
    # ponytail: self-check — Windows phải dùng Scripts/python.exe, không bin/python
    sr = Path(__file__).resolve().parents[2]
    venv_py = sr / ".venv-demucs" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    # Chưa cài vẫn ok — _demucs_python sẽ tạo; chỗ này chỉ check path shape
    if sys.platform == "win32":
        assert "Scripts" in str(venv_py)
    ratio = 0.05
    gain = min(3.5, max(1.5, 0.15 / max(ratio, 0.001)))
    assert gain > 1.5
    print("mux self-check ok:", venv_py)
