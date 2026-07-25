"""Portable system font resolution for burn/export (Win / macOS / Linux)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_VI_PROBE = "ĂÂĐÊÔƠƯăâđêôơưáàảãạắằấầếềốồớờứừýỳệỗộỹỐồủỹ"

# Chỉ tên file / stem — resolve qua _system_font_dirs() (Win / macOS / Linux).
_REF_FONT_NAMES = (
    "Arial Unicode.ttf",
    "Arial.ttf",
    "arial.ttf",
    "DejaVuSans.ttf",
    "NotoSans-Regular.ttf",
    "NotoSansCJK-Regular.ttc",
)


def _glyph_ink(font: Any, ch: str, size: int = 48) -> int:
    from PIL import Image, ImageDraw

    img = Image.new("L", (size * 3, size * 3), 0)
    ImageDraw.Draw(img).text((4, 4), ch, font=font, fill=255)
    return int(sum(img.getdata()))


def _system_font_dirs() -> list[Path]:
    """Thư mục font theo OS — không hardcode 1 máy."""
    dirs: list[Path] = []
    # Bundled fonts first: preview and export must use the exact same bytes.
    here = Path(__file__).resolve()
    for rel in ("fonts", "assets/fonts", "../../fonts"):
        dirs.append((here.parent / rel).resolve())
    try:
        # Frozen app: _internal/pipeline/export/fonts.py → _internal/dist/fonts.
        dirs.append((here.parents[2] / "dist" / "fonts").resolve())
    except IndexError:
        pass
    try:
        # Repository / frontend build.
        repo_root = here.parents[3]
        dirs.append((repo_root / "frontend" / "public" / "fonts").resolve())
        dirs.append((repo_root / "frontend" / "dist" / "fonts").resolve())
    except IndexError:
        pass
    env = os.environ.get("VIDEOCLONE_FONT_DIR") or os.environ.get("FONT_DIR")
    if env:
        dirs.append(Path(env))
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        dirs.append(Path(windir) / "Fonts")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    dirs.extend(
        [
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
            Path("/Library/Fonts"),
            Path.home() / "Library" / "Fonts",
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path.home() / ".fonts",
            Path.home() / ".local" / "share" / "fonts",
        ]
    )
    out: list[Path] = []
    seen: set[str] = set()
    for d in dirs:
        try:
            key = str(d)
            if key in seen or not d.is_dir():
                continue
            seen.add(key)
            out.append(d)
        except OSError:
            continue
    return out


_font_file_index: dict[str, str] | None = None


def _font_index() -> dict[str, str]:
    """Map lowercase filename → absolute path (quét 1 lần)."""
    global _font_file_index
    if _font_file_index is not None:
        return _font_file_index
    idx: dict[str, str] = {}
    for root in _system_font_dirs():
        try:
            # Linux: fonts nằm sâu (truetype/dejavu/...)
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in (".ttf", ".otf", ".ttc", ".otc"):
                    continue
                name = p.name.lower()
                # first win — bundled dirs come before host fonts
                idx.setdefault(name, str(p.resolve()))
        except OSError:
            continue
    _font_file_index = idx
    return idx


def _resolve_font_name(name: str) -> str | None:
    """Tìm file font theo tên (không path tuyệt đối)."""
    n = (name or "").strip()
    if not n:
        return None
    # Cho phép path tuyệt đối nếu caller truyền (tests / config)
    p = Path(n)
    if p.is_file():
        return str(p.resolve())
    idx = _font_index()
    hit = idx.get(n.lower())
    if hit:
        return hit
    # stem match: "Arial Bold" → arial bold.ttf
    stem = Path(n).stem.lower()
    for k, v in idx.items():
        if Path(k).stem.lower() == stem:
            return v
    return None


def _resolve_font_names(*names: str) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for n in names:
        path = _resolve_font_name(n)
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    return tuple(out)


def _ref_ink_map(sample: str, size: int = 48) -> dict[str, int]:
    from PIL import ImageFont

    need = {c for c in sample if not c.isspace()}
    for p in _resolve_font_names(*_REF_FONT_NAMES):
        try:
            font = ImageFont.truetype(p, size)
        except OSError:
            continue
        m = {ch: _glyph_ink(font, ch, size) for ch in need}
        if all(v > 200 for v in m.values()):
            return m
    return {}


def _font_covers_text(path: str, sample: str, size: int = 48) -> bool:
    """True if font draws sample glyphs (not empty / not tofu □)."""
    try:
        from PIL import ImageFont
    except Exception:
        return Path(path).exists()
    try:
        font = ImageFont.truetype(path, size)
    except OSError:
        return False
    need = {c for c in sample if not c.isspace()}
    if not need:
        return True
    ref = _ref_ink_map(sample, size)
    try:
        question_sig = (
            font.getbbox("?"),
            round(float(font.getlength("?")), 3),
            bytes(font.getmask("?")),
        )
    except Exception:
        question_sig = None
    # tofu box ink is nearly constant across missing VI glyphs
    inks: list[int] = []
    for ch in need:
        ink = _glyph_ink(font, ch, size)
        inks.append(ink)
        if ink < 30:
            return False
        if hasattr(font, "getbbox"):
            bb = font.getbbox(ch)
            if not bb or bb[2] <= bb[0]:
                return False
        # Some fonts map unsupported Unicode directly to their visible "?"
        # glyph, so non-empty ink alone is not proof of coverage.
        if question_sig is not None and ch != "?":
            try:
                if (
                    font.getbbox(ch),
                    round(float(font.getlength(ch)), 3),
                    bytes(font.getmask(ch)),
                ) == question_sig:
                    return False
            except Exception:
                pass
        if ref:
            r = ref.get(ch, 0)
            if r > 200 and ink < max(80, int(r * 0.35)):
                return False
    if len(inks) >= 6:
        # many missing glyphs → identical square tofu ink
        uniq = {round(v / 500) for v in inks if v > 0}
        if len(uniq) <= 2 and min(inks) > 1000:
            # if almost all glyphs share ~same ink, likely tofu fallback
            lo, hi = min(inks), max(inks)
            if hi > 0 and (hi - lo) / hi < 0.08 and not ref:
                return False
    return True


_font_cache: dict[str, str] = {}


def _pick_font(candidates: tuple[str, ...], *, sample: str = _VI_PROBE, cache_key: str = "") -> str:
    key = cache_key or sample
    hit = _font_cache.get(key)
    if hit and Path(hit).exists():
        return hit
    for p in candidates:
        if Path(p).exists() and _font_covers_text(p, sample):
            _font_cache[key] = p
            return p
    for p in candidates:
        if Path(p).exists():
            _font_cache[key] = p
            return p
    return "Arial"


# preset id (CAPTION_FONT_PRESETS FE) → tên file ưu tiên (mọi OS)
_FONT_PRESET_NAMES: dict[str, tuple[str, ...]] = {
    "system": (
        "NotoSans-Bold.ttf",
        "segoeuib.ttf", "SegoeUI-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf",
        "DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf", "Helvetica.ttc",
    ),
    "segoe": (
        "Inter-Bold.ttf",
        "segoeuib.ttf", "segoeui.ttf", "SegoeUI-Bold.ttf", "Segoe UI.ttf",
    ),
    "arial": (
        "Arimo-Bold.ttf",
        "arialbd.ttf", "arial.ttf", "Arial Bold.ttf", "Arial.ttf",
        "DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf",
    ),
    "bold": (
        "ArchivoBlack-Regular.ttf",
        "arialbd.ttf", "ariblk.ttf", "Arial Bold.ttf", "Arial Black.ttf",
        "DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf",
    ),
    "helvetica": (
        "Roboto-Bold.ttf",
        "Helvetica.ttc", "HelveticaNeue.ttc", "arialbd.ttf", "Arial Bold.ttf",
        "DejaVuSans-Bold.ttf",
    ),
    "verdana": (
        "OpenSans-Bold.ttf",
        "verdanab.ttf", "verdana.ttf", "Verdana Bold.ttf", "Verdana.ttf",
        "DejaVuSans-Bold.ttf",
    ),
    "tahoma": (
        "Carlito-Bold.ttf",
        "tahomabd.ttf", "tahoma.ttf", "Tahoma Bold.ttf", "Tahoma.ttf",
    ),
    "trebuchet": (
        "FiraSans-Bold.ttf",
        "trebucbd.ttf", "trebuc.ttf", "Trebuchet MS Bold.ttf", "Trebuchet MS.ttf",
    ),
    "rounded": ("Nunito-Bold.ttf", "NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf"),
    "impact": ("Anton-Regular.ttf", "impact.ttf", "Impact.ttf"),
    "georgia": (
        "Merriweather-Bold.ttf",
        "georgiab.ttf", "georgia.ttf", "Georgia Bold.ttf", "Georgia.ttf",
    ),
    "times": (
        "Tinos-Bold.ttf",
        "timesbd.ttf", "times.ttf", "Times New Roman Bold.ttf", "Times New Roman.ttf",
        "LiberationSerif-Bold.ttf",
    ),
    "palatino": (
        "Literata-Bold.ttf",
        "palab.ttf", "pala.ttf", "Palatino Linotype Bold.ttf",
    ),
    "garamond": ("EBGaramond-Bold.ttf", "garabd.ttf", "gara.ttf", "Garamond.ttf"),
    "courier": (
        "CourierPrime-Bold.ttf",
        "courbd.ttf", "cour.ttf", "Courier New Bold.ttf", "Courier New.ttf",
        "DejaVuSansMono-Bold.ttf",
    ),
    "mono": (
        "NotoSansMono-Bold.ttf",
        "consolab.ttf", "consola.ttf", "DejaVuSansMono-Bold.ttf",
    ),
    "comic": (
        "ComicNeue-Bold.ttf",
        "comicbd.ttf", "comic.ttf", "Comic Sans MS Bold.ttf",
    ),
    "cjk": (
        "NotoSansSC-Bold.ttf",
        "msyhbd.ttc", "msyh.ttc", "msyhbd.ttf", "msyh.ttf",
        "PingFang.ttc", "NotoSansCJK-Regular.ttc", "NotoSansCJK-Bold.ttc",
        "SourceHanSansSC-Regular.otf", "WenQuanYiMicroHei.ttf",
    ),
    "meiryo": (
        "NotoSansJP-Bold.ttf",
        "meiryob.ttc", "meiryo.ttc", "YuGothic-Bold.otf", "NotoSansCJKjp-Regular.otf",
    ),
    "malgun": (
        "NotoSansKR-Bold.ttf",
        "malgunbd.ttf", "malgun.ttf", "AppleSDGothicNeo.ttc", "NotoSansCJKkr-Regular.otf",
    ),
}

_SUBTITLE_BOLD_NAMES = (
    "NotoSans-Bold.ttf",
    "arialbd.ttf", "segoeuib.ttf", "arial.ttf", "segoeui.ttf",
    "Arial Bold.ttf", "Arial Unicode.ttf", "Arial.ttf",
    "DejaVuSans-Bold.ttf", "DejaVuSans.ttf",
    "NotoSans-Bold.ttf", "NotoSans-Regular.ttf",
    "PingFang.ttc", "NotoSansCJK-Regular.ttc", "Helvetica.ttc",
)

_SUBTITLE_VERT_NAMES = (
    "arialbd.ttf", "segoeuib.ttf", "Arial Bold.ttf",
    "Verdana Bold.ttf", "Tahoma Bold.ttf", "Trebuchet MS Bold.ttf",
    "Arial Unicode.ttf", "HelveticaNeue.ttc", "SFNS.ttf",
    "DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "NotoSans-Bold.ttf",
)


def _font_for_preset(preset_id: str) -> str:
    """Trả font file cho preset FE — resolve theo OS, không hardcode C:/Windows."""
    names = _FONT_PRESET_NAMES.get(preset_id or "") or ()
    candidates = _resolve_font_names(*names)
    if candidates:
        hit = _pick_font(candidates, cache_key=f"preset_{preset_id}")
        if hit != "Arial" or candidates:
            return hit
    return _subtitle_font()


def _subtitle_font() -> str:
    """Khớp preview `font-bold` — Arial/Segoe/DejaVu/Noto tùy máy."""
    return _pick_font(
        _resolve_font_names(*_SUBTITLE_BOLD_NAMES),
        cache_key="sub_bold",
    )


def _subtitle_font_vertical() -> str:
    """Font đậm title dọc (VI/Latin)."""
    return _pick_font(
        _resolve_font_names(*_SUBTITLE_VERT_NAMES),
        cache_key="vert",
    )

