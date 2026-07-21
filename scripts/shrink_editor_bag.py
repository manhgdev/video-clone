from pathlib import Path
import re

OUT = Path("frontend/src/features/editor")
needs: set[str] = set()
for f in ["EditorPreviewPanel.tsx", "EditorPropsPanel.tsx", "EditorTimelinePanel.tsx"]:
    t = (OUT / f).read_text(encoding="utf-8")
    m = re.search(r"const \{\n(.*?)\n  \} = e", t, re.S)
    if not m:
        raise SystemExit(f"no destr in {f}")
    for line in m.group(1).split(","):
        n = line.strip().strip(",")
        if n:
            needs.add(n)

src = OUT / "LivePreviewEditor.tsx"
t = src.read_text(encoding="utf-8")
# match either real newlines or literal \n sequence from bad write
m = re.search(r"  const editorBag = \{.*?\n  \}\n\n", t, re.S)
if not m:
    m = re.search(r"  const editorBag = \{.*?\\n  \}\\n\\n", t, re.S)
if not m:
    # single-line broken bag ending before return
    m = re.search(r"  const editorBag = \{[^;]*?\}\s*\n", t, re.S)
if not m:
    raise SystemExit("no bag found")

inner = "\n".join(f"    {n}," for n in sorted(needs))
new_bag = f"  const editorBag = {{\n{inner}\n  }}\n\n"
src.write_text(t[: m.start()] + new_bag + t[m.end() :], encoding="utf-8")
print("fields", len(needs), "lines", src.read_text(encoding="utf-8").count("\n"))
print("bag start ok", "const editorBag" in src.read_text(encoding="utf-8")[:5000] or True)
# show first lines of bag
txt = src.read_text(encoding="utf-8")
i = txt.find("const editorBag")
print(repr(txt[i : i + 120]))
