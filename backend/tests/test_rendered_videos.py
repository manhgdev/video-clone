from pathlib import Path

from api.routes import rendered


def test_render_list_keeps_versions_and_thumbnail_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(rendered, "PUBLIC_DATA", tmp_path)
    monkeypatch.setattr(rendered, "video_size", lambda _path: (1920, 1080))
    monkeypatch.setattr(rendered, "ffprobe_duration", lambda _path: 12.5)
    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "project.mp4").write_bytes(b"latest")
    (exports / "project-100.mp4").write_bytes(b"first")
    (exports / "project-200.mp4").write_bytes(b"second")
    (exports / "project-200.json").write_text('{"name":"Bản đẹp"}', encoding="utf-8")

    rows = rendered.list_rendered_videos()
    assert {row["renderId"] for row in rows} == {"project-100", "project-200"}
    assert all(row["projectId"] == "project" for row in rows)
    assert rows[0]["thumbnailUrl"].startswith("/api/renders/")
    assert next(row for row in rows if row["renderId"] == "project-200")["name"] == "Bản đẹp"
    saved = rendered.api_rename_render("project-100", rendered.RenderRenameIn(name=" Bản mới "))
    assert saved["name"] == "Bản mới"
    assert '"name": "Bản mới"' in (exports / "project-100.json").read_text(encoding="utf-8")

    calls = []
    def fake_run(args, **_kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b"jpg")

    monkeypatch.setattr(rendered.subprocess, "run", fake_run)
    first = rendered.ensure_thumbnail("project-200")
    second = rendered.ensure_thumbnail("project-200")
    assert first == second
    assert first.read_bytes() == b"jpg"
    assert len(calls) == 1
