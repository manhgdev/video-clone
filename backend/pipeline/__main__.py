"""python -m pipeline → self_check."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .export.burn import _cover_box_fit, cover_and_burn
from .core.config import DATA, EL_ADAM
from .core.media import hardware
from .export.mux import _bg_duck_expr
from .core.project import preview_tag
from .translate import _clean_burn_text, _needs_google_fallback
from .tts import EL_MODEL, EL_TTS_VER, _el_lang_code, _el_voice_id, list_voices, tts_cache_key

def _self_check() -> None:
    assert hardware()["accel"] in {"metal", "cuda", "cpu"}
    assert _el_voice_id(f"el:{EL_ADAM}") == EL_ADAM
    assert _el_voice_id("Linh") is None
    assert tts_cache_key("a", "v", "vi", "natural") == tts_cache_key("a", "v", "vi", "natural")
    assert tts_cache_key("a", "v", "vi", "natural") != tts_cache_key("b", "v", "vi", "natural")
    assert list_voices()
    assert _needs_google_fallback(
        "DADUEGOHO",
        "DADUEGOHO không phải là một câu và không thể dịch được",
        target_lang="vi",
    )
    assert _needs_google_fallback(
        "你连这个杀鸡都不敢看",
        "Bạn xử lý việc bạn даже не осмеливаетесь смотреть на убийство курицы.",
        target_lang="vi",
    )
    assert not _needs_google_fallback("你好", "Xin chào", target_lang="vi")
    assert not _clean_burn_text(
        "Bạn даже не осмеливаетесь смотреть",
        target_lang="vi",
    )
    # volume= expr dùng 4 chữ số thập phân (ffmpeg parse ổn định hơn)
    assert _bg_duck_expr([]) == "0.3500"
    assert (
        _bg_duck_expr([{"start": 1, "end": 2}])
        == "if(between(t\\,1.000\\,2.000)\\,0.1200\\,0.3500)"
    )
    assert preview_tag(20) == "p20" and preview_tag(0) == "full"
    # mode over (tight=True): cover chỉ bám OCR — text_box lớn không phình ROI.
    # tight=False cố ý nới theo caption (below/above nằm NGOÀI dải che).
    ocr = [(100, 400, 300, 440)]
    huge = (50, 200, 900, 700)
    fit = _cover_box_fit(ocr, huge, 1080, 1920, tight=True)
    assert fit is not None
    fx0, fy0, fx1, fy1 = fit
    assert fx1 - fx0 <= (300 - 100) + 20 + 2  # pad_x*2
    assert fy1 - fy0 <= (440 - 400) + 16 + 2  # pad_y*2
    assert fy0 >= 390 and fy1 <= 450
    assert _el_lang_code("vi") == "vi"
    assert _el_lang_code(None, "Xin chào") == "vi"
    assert _el_lang_code("en", "Xin chào") == "vi"
    assert EL_MODEL == "eleven_v3"
    assert EL_TTS_VER
    assert shutil.which("ffmpeg"), "ffmpeg required"
    src = Path(__file__).resolve().parents[2] / "ex_video" / "16_9.mp4"
    if src.exists():
        probe = DATA / "_probe" / "cover_smoke.mp4"
        probe.parent.mkdir(parents=True, exist_ok=True)
        subprocess.check_call(
            ["ffmpeg", "-y", "-ss", "5", "-t", "3", "-i", str(src), "-c", "copy", str(probe)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        out = DATA / "_probe" / "cover_out.mp4"
        cover_and_burn(
            probe,
            [
                {
                    "id": "t1",
                    "index": 1,
                    "start": 0.0,
                    "end": 3.0,
                    "source": "测试",
                    "translation": "Áp suất ở đây gấp 1000 lần mặt đất",
                }
            ],
            out,
            cover=True,
            burn=True,
        )
        assert out.exists() and out.stat().st_size > 1000, out
        # grab frame to verify cover
        shot = DATA / "_probe" / "cover_frame.jpg"
        subprocess.check_call(
            ["ffmpeg", "-y", "-ss", "1", "-i", str(out), "-frames:v", "1", str(shot)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("cover+burn ok", out)
    print("pipeline ok", hardware())


if __name__ == "__main__":
    _self_check()
