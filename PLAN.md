# PLAN tối ưu tốc độ — cập nhật 2026-07-27

> **Tiến độ:** ✅ P0 XONG (2026-07-27) — main gộp đủ 4 nhánh, 88 test pass, build OK,
> burn smoke OK. Đang chờ: P1 (ffmpeg vẽ — làm kế tiếp), P2, P3, P4, P5.

Mục tiêu: video vài tiếng xuất xong trong **phút**, không phải giờ; máy không đơ khi chạy.
Nguyên tắc: mỗi phase xong phải **đo số + chạy test** rồi mới sang phase sau; mọi thay đổi
render đều phải qua kiểm tra parity với preview (WYSIWYG).

Hiện trạng nhánh (quan trọng — làm P0 trước mọi thứ):

| Nhánh | Chứa gì | Trạng thái |
|---|---|---|
| `main` (2dfeec5) | prefetch decode, cancel-kill, ttsBake, auto-bake 0.8→1×, worker python probe | đang đứng |
| `fix/ocr-gpu-decode` (2 commit, đã push) | NVDEC batch decode (CPU 4,2→1,1 core, 41s→9,7s/48 câu), gộp 1 chế độ OCR, kẹp affinity + giới hạn luồng ONNX, nhãn GPU/CPU trên status, **fix xuất ra 0.8 khi timeline 1×**, 5+3+2 test mới | chờ merge |
| `claude/sleepy-allen-*` | tách system_check/ package + locate_worker.py | chờ merge, SẼ conflict locate.py |
| `claude/amazing-perlman-*` | tách TtsStudio + App | chờ merge |
| `claude/adoring-bell-*` | tách LivePreviewEditor thành hook/panel | chờ merge |

---

## P0 — Gộp việc đã xong (≈1 buổi, rủi ro thấp, ăn ngay)

**0.1. Merge `fix/ocr-gpu-decode` → `main`.**
- Toàn bộ đã verify từ trước (86 test pass, bench có số). Merge thẳng, chạy lại:
  `pytest` (bỏ nhóm test môi trường), `python -m pipeline`, bench
  `Định vị OCR` trên clip 30s — kỳ vọng ≤10s/48 câu, CPU ≤1,5 core, status hiện `GPU`.
- Lý do làm ĐẦU TIÊN: các phase sau sửa cùng file (`locate.py`, `render.py`) —
  merge muộn = conflict chồng conflict.

**0.2. Merge 3 nhánh worktree, theo thứ tự:**
1. `claude/sleepy-allen` (system_check + locate) — conflict `locate.py` chắc chắn:
   giải theo hướng **giữ logic của main mới** (NVDEC batch, 1 chế độ), áp cấu trúc
   tách file của nhánh (locate_worker.py). Sau merge: `pytest tests/ocr`, bench lại OCR.
2. `claude/amazing-perlman` (TtsStudio + App) — `tsc --noEmit` + smoke UI.
3. `claude/adoring-bell` (LivePreviewEditor) — `tsc --noEmit` + self-check FE
   (`__checkOcrOverlayLayout`, `__checkExportBakePlacement`) + xuất thử 1 video preview
   so khớp WYSIWYG.
- DoD: `main` chứa tất cả, working tree sạch, đủ bộ test xanh.

---

## P1 — Xuất khung nhanh 6–8× (≈2–3 buổi) — ƯU TIÊN CAO NHẤT SAU P0

Số đo hiện tại (clip 30s, 1080×1920@30): pipeline Python **69 fps**; ffmpeg thuần GPU
**586 fps**; mô phỏng ffmpeg blur+overlay 3 cue: **2,2s vs 13s**. Video 2h hiện ~52 phút
chỉ riêng bước này.

Gốc vấn đề: mọi khung đi GPU→RAM→Python→RAM→GPU (2h ≈ 1,3 TB qua pipe ×2 lượt).

**Cách làm — «Python chuẩn bị, ffmpeg vẽ»:**
1. **Giữ nguyên** phần chuẩn bị cue/layout trong `burn_parts/pipeline.py`
   (`cover_and_burn` đến hết đoạn tính `cue_fits`/`cue_overlays`) — đây là chỗ
   bảo đảm WYSIWYG, không đụng.
2. `cue_overlays` hiện là RGBA numpy → ghi ra `cache/burn_overlays/{cue_id}.png`.
3. Sinh `filter_complex_script` (đã có mẫu ở `retime_video_segments`):
   - mask blur: `crop` vùng + `boxblur`/`gblur` + `overlay` lại, `enable='between(t,cs,ce)'`
   - mask solid/mosaic: `drawbox`/`pixelize` (ffmpeg ≥7) hoặc pre-render PNG mask
   - chữ: `overlay=x:y:enable='between(t,bs,be)'` với PNG từng cue
   - LƯU Ý ffmpeg parser: đã đo được giới hạn ~100 nhánh expr → **chia lô ≤40 cue
     mỗi lệnh**, đoạn nào quá thì cắt video theo lô rồi concat (xem P2 — dùng chung hạ tầng).
4. Một lệnh: `-hwaccel cuda` in → filter → `h264_nvenc` + map audio từ input gốc.
   Frame KHÔNG rời ffmpeg.
5. **Gộp luôn crop/scale xuất cuối vào cùng lệnh** (thêm `crop,scale` cuối graph) —
   bỏ hẳn `encode_export_1080` lần 2 khi resolution trùng → tiết kiệm thêm ~30–40%.
6. Fallback tự động về đường Python cũ khi: không map được style, ffmpeg lỗi,
   hay biến `VIDEO_CLONE_LEGACY_BURN=1` (van thoát khi có bug).
7. **Parity gate (bắt buộc):** script test render 1 frame giữa mỗi cue bằng cả 2 đường,
   so pixel-diff vùng caption ≤ ngưỡng nhỏ; giữ nguyên `test_caption_css_parity`.
   Cắt 1 video mẫu có đủ 4 loại cue (ngang/mid/dọc/nhãn) + logo + effect làm fixture.

- File đụng: `burn_parts/pipeline.py` (thêm nhánh build-filter), file mới
  `burn_parts/ffgraph.py` (sinh graph + chạy), `render.py` giữ làm fallback.
- DoD: clip 30s ≤3s; parity pass; 86+ test xanh; video 1h đo thật ≤6 phút bước xuất khung.

## P2 — Chỉ encode đoạn có cue, copy phần còn lại (≈1–2 buổi, sau P1)

Video dài thường >50% thời lượng không có chữ.
1. Lấy danh sách keyframe: `ffprobe -skip_frame nokey -show_frames` (nhanh, chỉ đọc header).
2. Chia video thành «đoạn có cue» (mở rộng tới keyframe gần nhất 2 phía) và «đoạn trống».
3. Đoạn có cue → pipeline P1; đoạn trống → `-c copy` (0,1s cho 30s video — đã đo).
4. Nối bằng concat demuxer (`-f concat -safe 0 -c copy`), audio mux lại một lần cuối.
5. Test: A/V sync tại 3 mối nối (so timestamp frame), tổng duration ±0,1s.
- DoD: video 1h/20% cue: tổng thời gian giảm thêm ≥3×.

## P3 — Đo Demucs + TTS cho video dài (≈1 buổi ĐO trước, chưa sửa)

Sau P1/P2, hai khâu này thành nghẽn lớn nhất còn lại. Chưa có số → **đo trước, không đoán**:
- Demucs (`separate_no_vocals`): phút xử lý / giờ video, VRAM, đã cache theo fingerprint chưa,
  có chạy lại vô ích khi re-export không.
- TTS: giây / câu theo engine, tỉ lệ cache hit khi re-run.
- Ra quyết định sau khi có số (ứng viên: chunk Demucs + resume; skip Demucs khi
  originalAudioMode=original; TTS batch).
- DoD: bảng số + 1 trang kết luận ghi vào PLAN này.

## P4 — Frontend: bundle + hoàn tất tách file (sau P0.2)

- `manualChunks` / dynamic import cho editor (bundle hiện >600 KB — cảnh báo vite).
- LivePreviewEditor sau merge nhánh còn lại bao nhiêu dòng → tách nốt theo seam đã ghi
  (useSpeedTransaction / useDubAudioSync / useTimelineDrag).
- DoD: build không còn cảnh báo chunk; `tsc` sạch.

## P5 — Vệ sinh (xen kẽ lúc chờ)

- Test môi trường đỏ → `pytest.mark.skipif` theo điều kiện thật (thiếu fastapi/GPU/mạng)
  để `pytest -q` trần luôn xanh.
- `.gitignore`: chặn `*.bk*`, `backend/app.log`, `backend/public/**` khỏi staging (đã có rule,
  rà lại vì app.log đang tracked).
- Git ref từng bị ghi rỗng (2026-07-27): nếu tái diễn — `.git/logs/HEAD` lấy sha cuối,
  `git update-ref refs/heads/main <sha>`. Cân nhắc bật `core.fsyncMethod=fsync` trên máy này.

---

## Thứ tự tổng & ước lượng

| Phase | Ăn được gì | Effort |
|---|---|---|
| P0 | OCR nhanh 4×, máy hết đơ, hết lỗi xuất 0.8 — mọi thứ ĐÃ code xong | 1 buổi |
| P1 | Xuất khung 6–8× (2h video: 52ph → ~7ph) | 2–3 buổi |
| P2 | Thêm ~3× cho video nhiều khoảng trống | 1–2 buổi |
| P3 | Số liệu để quyết Demucs/TTS | 1 buổi |
| P4–P5 | UI mượt, repo sạch | xen kẽ |

Rủi ro chính: (1) conflict `locate.py` ở P0.2 — giải theo main mới; (2) parity P1 —
đã có gate; (3) giới hạn expr ffmpeg — đã biết ngưỡng ~100, chia lô 40.
