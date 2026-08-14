# Cấu trúc source

Tài liệu này mô tả **thư mục, trách nhiệm module và quy tắc phụ thuộc**. Không liệt kê mọi file con; không thay README (cài đặt, API đầy đủ, license).

---

## Frontend

```text
frontend/src/
├─ app/
│  ├─ App.tsx                 Shell, mode, upload/switch project, ProgressPopup
│  ├─ appMode.ts
│  ├─ appSettings.ts          load/persist settings + setup gate + idleStatus
│  └─ useProjectSession.ts    Facade appSettings + useSessionRestore (F5 mở lại project)
├─ pages/                     Trang cấp cao (Clone, Download, TTS, Renders, …)
├─ features/
│  ├─ configuration/          Cấu hình engine / API / setup
│  ├─ download/               Form + job download
│  ├─ editor/
│  │  ├─ LivePreviewEditor.tsx   Timeline + preview (orchestration UI)
│  │  └─ lib/                    Helper thuần (timeline, cover, dub math, …)
│  │     ├─ captionMeasure.ts    Đo chữ canvas + wrap dòng (nguồn sự thật layout)
│  │     ├─ coverBox.ts          Hình học khung che (pad/clamp/seed box)
│  │     └─ coverLayout.ts       Quyết định layout + bake WYSIWYG xuất
│  ├─ project/                API project, sidebar, types, compound
│  │  └─ use*.ts                 Hook luồng dài của App: useSegmentEditing,
│  │                             useProjectMedia (bake/rebake), useDubControl,
│  │                             useExportFlow, useJobPolling
│  └─ tts/                    TTS Studio UI + CSS
│     ├─ TtsStudio.tsx           Orchestration + state
│     ├─ Tts*Panel / TtsIcons    Panel con (input, history, clone) + icon nội bộ
│     └─ lib/                    Logic thuần (voiceDisplay, srt, download, format)
└─ shared/
   ├─ api/                    HTTP helper
   ├─ components/             ProgressPopup, Icons, …
   ├─ lib/                    cn, util
   ├─ types/
   └─ ui/                     resizable, scroll-area, …
```

### Quy ước frontend

- `App.tsx`: state/liên kết cấp app (project, status, dub/export/cancel). Luồng dài thuộc feature → hook hoặc file feature.
- `LivePreviewEditor` / `TtsStudio`: panel lớn; logic thuần đặt `features/*/lib/`, không nhét thêm helper vào component khổng lồ nếu có thể tách file.
- API + type theo domain: `project.api.ts`, `project.types.ts`.
- Lớp cũ `components/`, `lib/`, `services/` ở root `src/`: không thêm code mới; khi chạm, chuyển dần sang `shared/` hoặc feature nếu diff nhỏ.
- Không tạo wrapper/placeholder “cho chuẩn cấu trúc”.

---

## Backend

```text
backend/
├─ main.py                    Uvicorn entry → create_app()
├─ api/
│  ├─ app.py                  FastAPI app + CORS + static
│  ├─ deps.py                 Pydantic schemas + validators dùng chung
│  ├─ job_spawn.py            Thread spawn pipeline (bắt lỗi → set_status)
│  ├─ video_serve.py          Serve MP4 + Range an toàn
│  ├─ routes_all.py           Aggregator: include_router từng domain
│  └─ routes/                 HTTP theo domain
│     ├─ projects.py          upload, video, status, settings, rebake-speed
│     ├─ jobs.py              run, dub, cancel, export, output
│     ├─ segments.py          segments CRUD, compound
│     ├─ overlays.py
│     ├─ audio.py             no-vocals / cache / download audio
│     ├─ system.py            hardware, config, checks, install_*
│     ├─ tts_studio.py        studio synth / clone / voices patch
│     ├─ tts_voices.py
│     ├─ tts_preview.py
│     ├─ download.py
│     └─ rendered.py
├─ pipeline/
│  ├─ run.py                  Facade: run_pipeline / run_dub / run_export
│  ├─ translate.py            Facade MT
│  ├─ orchestrate/            Job nhiều bước
│  │  ├─ asr_translate.py
│  │  ├─ dub.py
│  │  ├─ export_job.py        run_export (điều phối)
│  │  ├─ export_overlays.py   TextOverlay editor → cue burn
│  │  ├─ export_outputs.py    Đóng gói mp4/cover/audio/SRT/GIF
│  │  └─ tts_fit.py
│  ├─ asr/                    faster-whisper
│  ├─ ocr/                    RapidOCR + extract_parts (runtime/scan/merge/…)
│  ├─ mt/                     free / ollama / cloud / text helpers
│  ├─ tts/                    manager, studio, engines (vieneu, capcut, …)
│  ├─ export/
│  │  ├─ burn.py              Facade cover_and_burn
│  │  ├─ burn_parts/          ass, layout_geo/_text (layout), draw_text (vẽ RGBA),
│  │  │                       ocr_boxes, pipeline (chuẩn bị cue), render (decode→mask→encode)
│  │  ├─ mux.py               Facade
│  │  ├─ stem.py              Demucs / no_vocals
│  │  ├─ mux_audio.py         mux_dub / mix TTS
│  │  ├─ fonts.py             Resolve font đa OS
│  │  ├─ cover_mask.py
│  │  ├─ srt.py
│  │  └─ …
│  ├─ download/               yt-dlp jobs
│  └─ core/                   Hạ tầng dùng chung
│     ├─ project.py           meta, layout, status
│     ├─ jobs.py              cancel flag + kill process tree + run_cmd
│     ├─ media.py             ffmpeg helpers, detect_device (facade re-export speed_timeline)
│     ├─ speed_timeline.py    Bake speed + timeline baseline 1× (remap, meta_baked_speed)
│     ├─ resources.py         adaptive_workers (trần CPU ~85%)
│     ├─ accel.py             Ưu tiên CUDA/MPS/CPU (VieNeu/Whisper/…)
│     ├─ system_check/        probe phần cứng + install gói AI + checks (facade __init__)
│     ├─ runtime_site.py      frozen .venv-runtime
│     └─ …
└─ …

tests/
└─ backend/                   Mirror theo domain backend
   ├─ api/  asr/  core/  download/  export/  mt/  ocr/  tts/
   ├─ pipeline/               Test module pipeline cấp cao (SRT, subtitles, …)
   ├─ video/                  Fixture video nhỏ
   └─ conftest.py             Thêm `backend/` vào Python path khi pytest

tests/fixtures/
├─ fonts/                     Video fixture kiểm tra font
├─ frames/                    Ảnh frame dọc/ngang dùng cho OCR và export
├─ subtitles/                 File SRT mẫu
└─ state/                     JSON state/DB mẫu

tests/manual/
└─ font/                      Script chẩn đoán font/layout thủ công + ảnh mẫu
```

### Quy ước backend

| Lớp | Trách nhiệm |
|---|---|
| `api/routes/*` | Parse/validate HTTP, gọi pipeline, map lỗi → status code |
| `pipeline/<domain>/` | Nghiệp vụ; **không** import FastAPI |
| `orchestrate/` | Phối hợp ≥2 domain (ASR→dịch, dub, export) |
| `core/` | Helper hạ tầng đã dùng bởi nhiều domain |
| Facade (`run.py`, `burn.py`, `mux.py`, `extract.py`, `translate.py`) | Re-export API ổn định sau khi tách file |

- Bug dùng chung: sửa **một lần** ở helper/pipeline gốc + một check nhỏ (`tests/backend/` hoặc assert) nếu logic không tầm thường.
- Job dài: `begin_job` / `arm_job` / `request_cancel` + `register_process` mọi subprocess để **Huỷ** kill được.
- GPU: `core/accel.py` là nguồn sự thật cho device preference; engine chỉ gọi helper, không hardcode path Windows.

---

## Desktop pack

```text
build_app/
├─ launcher.py       Cửa sổ + spawn API + VIDEO_CLONE_HOME
├─ build.mjs         Vite + PyInstaller
└─ release/          VideoClone_v* / (chạy cả thư mục)
```

AI nặng cài sau vào `%LOCALAPPDATA%\VideoClone\.venv-runtime` (và `.venv-ocr`), không nhét full vào EXE.

---

## Quy tắc phụ thuộc

```text
Frontend:  app/pages  →  features  →  shared
Backend:   api/routes  →  orchestrate | pipeline/<domain>  →  core
```

- `shared` / `core` **không** import ngược feature/domain cụ thể.
- Hai feature không import component nội bộ của nhau; phần dùng chung thật → `shared`.
- Route không gọi route khác; gọi chung hàm pipeline.
- `*.bk*`, `*_pre_v4`, backup xoay vòng: local only, không commit (xem `.gitignore`).

---

## Khi cập nhật tài liệu này

Chỉ sửa khi đổi:

1. Cây thư mục cấp domain, hoặc  
2. Trách nhiệm module (ai gọi ai), hoặc  
3. Quy tắc phụ thuộc / facade.

Không ghi changelog tính năng, hướng dẫn cài, hay danh sách endpoint đầy đủ — đặt ở **README.md**.
