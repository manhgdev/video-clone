"""Optional offline speaker diarization using Sherpa-ONNX."""
from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

SEGMENTATION_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
EMBEDDING_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
EMBEDDING_NAME = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"


def ensure_diarization_models(model_dir: Path, log=None) -> tuple[Path, Path]:
    """Download the two official Sherpa models once, with atomic destination writes."""
    model_dir.mkdir(parents=True, exist_ok=True)
    segmentation = model_dir / "model.int8.onnx"
    embedding = model_dir / EMBEDDING_NAME
    if segmentation.is_file() and embedding.is_file():
        return segmentation, embedding

    def report(message: str) -> None:
        if log:
            log(message + "\n")

    with tempfile.TemporaryDirectory(prefix="videoclone-diarization-") as tmp_raw:
        tmp = Path(tmp_raw)
        if not segmentation.is_file():
            report("Đang tải model phân đoạn người nói…")
            archive = tmp / "segmentation.tar.bz2"
            urllib.request.urlretrieve(SEGMENTATION_URL, archive)
            with tarfile.open(archive, "r:bz2") as bundle:
                member = next((m for m in bundle.getmembers() if Path(m.name).name == "model.int8.onnx" and m.isfile()), None)
                if member is None:
                    raise RuntimeError("Gói model diarization không chứa model.int8.onnx")
                source = bundle.extractfile(member)
                if source is None:
                    raise RuntimeError("Không đọc được model phân đoạn người nói")
                partial = model_dir / "model.int8.onnx.part"
                with source, partial.open("wb") as target:
                    shutil.copyfileobj(source, target)
                partial.replace(segmentation)
        if not embedding.is_file():
            report("Đang tải model nhận dạng giọng người nói…")
            partial = model_dir / f"{EMBEDDING_NAME}.part"
            urllib.request.urlretrieve(EMBEDDING_URL, partial)
            partial.replace(embedding)
    report("Đã cài model tách người nói.")
    return segmentation, embedding


def assign_speakers(segments: list[dict[str, Any]], turns: list[dict[str, Any]]) -> None:
    """Attach the speaker with the greatest temporal overlap to every ASR cue."""
    for segment in segments:
        start = float(segment.get("start") or 0)
        end = max(start, float(segment.get("end") or start))
        best = max(
            turns,
            key=lambda turn: max(0.0, min(end, float(turn["end"])) - max(start, float(turn["start"]))),
            default=None,
        )
        if best is not None:
            overlap = max(0.0, min(end, float(best["end"])) - max(start, float(best["start"])))
            if overlap > 0:
                segment["speaker"] = str(best["speaker"])


def diarization_provider_for_device(device: dict[str, Any]) -> str:
    """Map the shared video-clone hardware result to a Sherpa provider."""
    accel = str(device.get("accel") or "").lower()
    gpu_kind = str(device.get("gpuKind") or "").lower()
    if accel == "cuda" or gpu_kind == "nvidia":
        return "cuda"
    if accel in ("metal", "coreml", "mps") or gpu_kind == "apple":
        return "coreml"
    # Sherpa's documented Python providers do not include DirectML.
    # AMD/Intel Windows therefore use the optimized multi-thread CPU path.
    return "cpu"


def preferred_diarization_provider() -> str:
    forced = os.getenv("SPEAKER_DIARIZATION_PROVIDER", "auto").strip().lower()
    if forced in ("cpu", "cuda", "coreml"):
        return forced
    try:
        from pipeline.core.media import detect_device

        device = detect_device()
        return diarization_provider_for_device(device)
    except Exception:
        return "cpu"


def diarize_audio(
    audio_path: Path,
    model_dir: Path,
    num_speakers: int = 0,
    provider_out: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    try:
        import numpy as np
        import sherpa_onnx
        import soundfile as sf
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "Sherpa-ONNX chưa tải được native runtime. Vào Cấu hình → Thiết lập → "
            f"Cài tách người nói. Chi tiết: {exc}"
        ) from exc

    segmentation_env = os.getenv("SPEAKER_DIARIZATION_SEGMENTATION_MODEL", "").strip()
    embedding_env = os.getenv("SPEAKER_DIARIZATION_EMBEDDING_MODEL", "").strip()
    segmentation_path = Path(segmentation_env) if segmentation_env else model_dir / "model.int8.onnx"
    embedding_path = Path(embedding_env) if embedding_env else model_dir / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
    if not segmentation_env and not embedding_env:
        segmentation_path, embedding_path = ensure_diarization_models(model_dir)
    elif not segmentation_path.is_file() or not embedding_path.is_file():
        raise RuntimeError(f"Thiếu model diarization trong {model_dir}")

    samples, rate = sf.read(str(audio_path), dtype="float32", always_2d=True)
    samples = np.ascontiguousarray(samples[:, 0], dtype=np.float32)
    preferred = preferred_diarization_provider()
    providers = [preferred] if preferred == "cpu" else [preferred, "cpu"]
    last_error: Exception | None = None
    result = None
    for provider in providers:
        try:
            threads = 2 if provider in ("cuda", "coreml") else max(1, min(8, (os.cpu_count() or 4) - 1))
            segmentation = sherpa_onnx.OfflineSpeakerSegmentationModelConfig()
            segmentation.pyannote.model = str(segmentation_path)
            segmentation.provider = provider
            segmentation.num_threads = threads
            embedding = sherpa_onnx.SpeakerEmbeddingExtractorConfig()
            embedding.model = str(embedding_path)
            embedding.provider = provider
            embedding.num_threads = threads
            clustering = sherpa_onnx.FastClusteringConfig(
                num_clusters=num_speakers if num_speakers >= 2 else -1, threshold=0.9,
            )
            config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=segmentation, embedding=embedding, clustering=clustering,
                min_duration_on=0.3, min_duration_off=0.5,
            )
            if not config.validate():
                raise RuntimeError(f"Sherpa provider {provider} không khả dụng")
            diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
            if int(rate) != int(diarizer.sample_rate):
                raise RuntimeError(f"Audio phải là {diarizer.sample_rate} Hz")
            result = diarizer.process(samples).sort_by_start_time()
            if provider_out is not None:
                provider_out["provider"] = provider
            break
        except Exception as exc:
            last_error = exc
    if result is None:
        raise RuntimeError(f"Không chạy được speaker diarization: {last_error}") from last_error
    return [
        {"start": float(turn.start), "end": float(turn.end), "speaker": f"SPEAKER_{int(turn.speaker):02d}"}
        for turn in result if float(turn.end) - float(turn.start) >= 0.05
    ]
