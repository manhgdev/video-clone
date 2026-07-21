"""Split LivePreviewEditor into preview/props/timeline panels with editorBag bag."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend/src/features/editor"
SRC = OUT / "LivePreviewEditor.tsx"
BK = OUT / "LivePreviewEditor.tsx.bk3"

RANGES = {
    "preview": (4351, 5158),
    "props": (5163, 6131),
    "timeline": (6139, 7012),
}
EXPORTS = {
    "preview": "EditorPreviewPanel",
    "props": "EditorPropsPanel",
    "timeline": "EditorTimelinePanel",
}

PANEL_IMPORTS = '''/* Split from LivePreviewEditor.tsx */
import React from 'react'
import { cn } from '@/shared/lib/cn'
import { ResizablePanel } from '@/shared/ui/resizable'
import { ScrollArea } from '@/shared/ui/scroll-area'
import {
  TabSvg,
  AspectIcon,
  TimelineFilmstrip,
  PanelView,
  PropLabel,
  NumField,
  TrackCtrl,
  TlButton,
  COVER_MASK_STYLES,
  CAPTION_FONT_PRESETS,
  CAPTION_LANE_DEFS,
  ASPECT_PRESETS,
  ASSET_TABS,
  EFFECT_PRESETS,
  FONT_SIZES,
  captionChromeStyle,
  captionFontCss,
  captionFontStyle,
  coverMaskPreviewStyle,
  sourceToDisplayStyle,
  videoCropStyle,
  formatTime,
  formatTimecode,
  overlayDisplayFontStyle,
  isOcrOverlayLayout,
  captionLaneOf,
} from '@/features/editor/lib'
import { EditorMaskPanel } from '@/features/editor/EditorMaskPanel'
import { IconHeadphones } from '@/shared/components/Icons'
import { fitOverlayFontPx, layoutOcrOverlay, midInsideVerticalWatermark } from '@/features/editor/ocrOverlayLayout'
import { logoFrame } from '@/features/editor/lib/logoMotion'
'''


def load_clean_source() -> str:
    if BK.exists():
        t = BK.read_text(encoding="utf-8")
        if t.count("\n") > 7000:
            print("using bk3", t.count("\n"), "lines")
            return t
    t = SRC.read_text(encoding="utf-8")
    print("using current", t.count("\n"), "lines")
    return t


def declared_names(src: str) -> list[str]:
    names: set[str] = set()
    m = re.search(
        r"export default function LivePreviewEditor\(\s*\{([^}]*)\}",
        src,
        re.S,
    )
    if m:
        for part in m.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            nm = re.split(r"\s*=\s*", part, 1)[0].strip()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", nm):
                names.add(nm)
    for a, b in re.findall(
        r"const\s*\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]",
        src,
    ):
        names.add(a)
        names.add(b)
    for n in re.findall(r"\b(?:const|let)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=", src):
        names.add(n)
    for n in re.findall(r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", src):
        names.add(n)
    # drop component itself and tiny UI helpers defined outside main
    names.discard("LivePreviewEditor")
    names.discard("loadTimelineTool")
    names.discard("PanelView")
    names.discard("PropLabel")
    names.discard("NumField")
    names.discard("TrackCtrl")
    names.discard("CtxItem")
    names.discard("CtxSep")
    names.discard("TlButton")
    return sorted(names)


def used_in(body: str, bag: set[str]) -> list[str]:
    used = []
    for n in sorted(bag, key=len, reverse=True):
        if re.search(rf"(?<![\w.]){re.escape(n)}\b", body):
            used.append(n)
    return sorted(used)


def make_panel(export: str, body: str, need: list[str]) -> str:
    destr = ",\n    ".join(need) if need else ""
    indented = "".join(("  " + ln) if ln.strip() else ln for ln in body.splitlines(True))
    return f"""{PANEL_IMPORTS}
/** Parent locals bag from LivePreviewEditor. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function {export}({{ e }}: {{ e: any }}) {{
  const {{
    {destr}
  }} = e
  return (
{indented}  )
}}
"""


def main() -> None:
    text = load_clean_source()
    lines = text.splitlines(keepends=True)
    bag = declared_names(text)
    print("bag", len(bag))
    tmp = OUT / "_split_tmp"
    tmp.mkdir(exist_ok=True)
    (tmp / "declared.txt").write_text("\n".join(bag), encoding="utf-8")

    needs: dict[str, list[str]] = {}
    for key, (a, b) in RANGES.items():
        body = "".join(lines[a - 1 : b])
        need = used_in(body, set(bag))
        needs[key] = need
        export = EXPORTS[key]
        code = make_panel(export, body, need)
        path = OUT / f"{export}.tsx"
        path.write_text(code, encoding="utf-8")
        print(f"{export}: need={len(need)} lines={body.count(chr(10))+1}")

    # editorBag before main return
    bag_inner = ",\n".join("    " + n for n in bag)
    bag_const = f"  const editorBag = {{\n{bag_inner},\n  }}\n\n"
    marker = 'className="live-preview-editor-root'
    pos = text.find(marker)
    if pos < 0:
        raise SystemExit("main root not found")
    # find start of `return (` before marker
    ret = text.rfind("return (", 0, pos)
    if ret < 0:
        raise SystemExit("return not found")
    # only the outermost return of component — should be indented with 2 spaces
    # walk back to line start
    line_start = text.rfind("\n", 0, ret) + 1
    text2 = text[:line_start] + bag_const + text[line_start:]
    lines2 = text2.splitlines(keepends=True)
    added = bag_const.count("\n")
    ranges2 = {k: (a + added, b + added) for k, (a, b) in RANGES.items()}

    for k, (a, b) in ranges2.items():
        print(k, a, lines2[a - 1].strip()[:70])

    pre = "".join(lines2[: ranges2["preview"][0] - 1])
    mid_pp = "".join(lines2[ranges2["preview"][1] : ranges2["props"][0] - 1])
    mid_pt = "".join(lines2[ranges2["props"][1] : ranges2["timeline"][0] - 1])
    post = "".join(lines2[ranges2["timeline"][1] :])

    import_line = (
        "import { EditorPreviewPanel } from '@/features/editor/EditorPreviewPanel'\n"
        "import { EditorPropsPanel } from '@/features/editor/EditorPropsPanel'\n"
        "import { EditorTimelinePanel } from '@/features/editor/EditorTimelinePanel'\n"
    )
    if "EditorPreviewPanel" not in pre:
        needle = "import { EditorMaskPanel } from '@/features/editor/EditorMaskPanel'\n"
        if needle in pre:
            pre = pre.replace(needle, needle + import_line)
        else:
            pre = pre.replace(
                "from '@/features/editor/ocrOverlayLayout'\n",
                "from '@/features/editor/ocrOverlayLayout'\n" + import_line,
                1,
            )

    insert = (
        "              <EditorPreviewPanel e={editorBag} />\n"
        + mid_pp
        + "              <EditorPropsPanel e={editorBag} />\n"
        + mid_pt
        + "              <EditorTimelinePanel e={editorBag} />\n"
    )
    new_text = pre + insert + post
    SRC.write_text(new_text, encoding="utf-8")
    print("LPE lines", new_text.count("\n"))
    print("done")


if __name__ == "__main__":
    main()
