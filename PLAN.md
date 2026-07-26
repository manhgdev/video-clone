# PLAN tối ưu tốc độ — bản đầy đủ, cập nhật 2026-07-27

Mục tiêu: video vài tiếng xuất xong trong **phút**, không phải giờ; máy không đơ khi chạy.
Nguyên tắc: mỗi phase xong phải **đo số + chạy test** rồi mới sang phase sau; mọi thay đổi
render đều phải qua kiểm tra parity với preview (WYSIWYG).

## Tiến độ tổng

| Phase | Nội dung | Trạng thái |
|---|---|---|
| P0 | Gộp 4 nhánh (OCR-GPU, system_check, TtsStudio/App, LivePreviewEditor) | ✅ 2026-07-27 |
| P1 | ffmpeg vẽ mask+chữ trong filter graph | ✅ 2026-07-27 (`e75027c`) |
| P2 | Chỉ encode đoạn có cue, copy phần trống | ✅ 2026-07-27 (`f3143f7`) |
| P1.5 | Gộp crop/scale vào burn + xử lý logo fade | ✅ 2026-07-27 (`5352464`) |
| P3 | Đo Demucs + TTS video dài (đo trước, chưa sửa) | ✅ 2026-07-27 — bảng dưới |
| P4 | FE: bundle split + tách nốt LivePreviewEditor | ⬜ |
| P5 | Vệ sinh: skipif test môi trường, app.log, git fsync | ⬜ xen kẽ |

## Số đo đã chốt (clip 30s, 1080×1920@30, GTX 1660 SUPER)

- Pipeline Python cũ: **69 fps** (giải mã NVDEC→RAM 85 · Pillow vẽ 72/luồng · NVENC 147);
  gốc nghẽn: mọi khung đi GPU→RAM→Python→RAM→GPU (2h ≈ 1,3 TB qua pipe ×2 lượt).
- ffmpeg thuần GPU: **586 fps**.
- Burn clip 30s/2 cue: legacy **12,3s** → P1 full graph **5,9s** → P2 segmented **3,75s**.
- P2 kiểm chứng: 902/902 khung, đoạn trống bit-identical với nguồn, decode sạch;
  segment muxer `-segment_times (K − nửa_khung)` = cắt packet-chính-xác tại keyframe K
  (đường `-ss/-to` lệch +2 khung vì DTS B-frame — đã loại).
- OCR định vị 48 câu: 41s/4,2 core → **9,7s/1,1 core**, hủy ~0,5s, status hiện `GPU`.
- Ngoại suy video 2h: bước xuất khung 52ph → **~7–10ph** (P1), phần trống gần miễn phí (P2).

---

## P1.5 — Gộp crop/scale vào burn, xóa fallback logo (≈1 buổi)

**Hiện trạng đã khảo sát (đọc `export_job.py` + `media.py`):**
- `encode_export_1080` ĐÃ có fast-path `-c:v copy` khi: không crop + codec h264 +
  đúng chiều cao đích ([media.py:1107](backend/pipeline/core/media.py)). Tức là encode
  lần 2 **chỉ** xảy ra khi người dùng đổi khung hình (aspect/crop) hoặc độ phân giải
  khác nguồn — nhưng khi đó lại là re-encode **toàn bộ** video, phá luôn lợi ích P2
  (đoạn trống copy xong vẫn bị encode lại ở bước sau).
- Chuỗi hiện tại: `retime_video_segments` → `cover_and_burn` (P1/P2) →
  `mux_dub`/`mux_original_audio` (video copy) → `encode_export_1080(out, out, target, crop)`
  tại [export_job.py:476](backend/pipeline/orchestrate/export_job.py).

**Việc làm:**
1. Truyền `crop_box` + `target_height` xuống `try_render_ffmpeg` (tính sẵn ở
   `export_job.py` TRƯỚC khi gọi `cover_and_burn`, vì sau burn mới `video_size(out)`
   là quá muộn — chú ý `resolve_export_crop` đang đo trên `out` sau mux; chuyển sang
   đo trên video nguồn burn, cùng kích thước).
2. Full graph: nối `crop=w:h:x:y,scale=…` vào cuối graph — mask/chữ vẫn tính theo
   toạ độ NGUỒN (trước crop) nên đặt crop/scale **sau** mọi overlay; WYSIWYG giữ nguyên.
3. Segmented + crop/scale: đoạn trống không thể copy nữa → đoạn trống encode bằng
   graph tối giản `crop,scale` (NVENC thuần ~586 fps, vẫn nhanh hơn nhiều so với
   để `encode_export_1080` chạy lại cả file), đoạn active nối `crop,scale` cuối graph.
   Guard `_SEG_MIN_SAVED/_SEG_MAX_COVERAGE` tính lại theo chi phí mới (đoạn trống
   không còn ~0 chi phí — ngưỡng "đáng làm" hạ xuống, đo rồi chỉnh).
4. Khi burn đã ra đúng crop+resolution: `encode_export_1080` tự rơi vào fast-path copy
   (điều kiện có sẵn) — KHÔNG bỏ hàm này, nó vẫn là lưới an toàn cho đường legacy.
5. **Logo fade/opacity** (gap P1 còn lại — đang fallback legacy toàn bộ): map sang
   ffmpeg `overlay` + `format=rgba,colorchannelmixer=aa=<opacity>` và fade bằng
   `fade=t=in:alpha=1` trên nhánh logo. Nếu ăn khớp pixel với legacy (test so khung
   giữa fade) thì xoá lý do fallback `logo` trong `_feasible()`.
6. Kiểm tra `retime_video_segments`: khi có `videoSpeed` từng câu, nó encode một lần
   TRƯỚC burn. Chưa đụng ở P1.5 (phức tạp, cần map thời gian phi tuyến vào graph) —
   ghi nhận số đo: bao nhiêu % export thực tế có retime, mất bao lâu. Quyết ở P3.

- File đụng: `burn_parts/ffgraph.py` (thêm tham số crop/scale + nhánh logo),
  `burn_parts/pipeline.py` (chữ ký), `orchestrate/export_job.py` (tính crop sớm).
- Test: thêm case crop 9:16→1:1 + resolution 720 vào `test_ffgraph_burn.py` —
  so khung với đường legacy + `encode_export_1080` (pixel-diff vùng caption ≤ ngưỡng,
  kích thước ra đúng); case logo PNG có opacity 50% + fade 0,5s.
- DoD: export có đổi khung/resolution chỉ còn **một** lần encode; suite xanh;
  đo clip 30s crop+720p: kỳ vọng ≤5s tổng (hiện ~9s vì double encode).

## P3 — Đo Demucs + TTS cho video dài (≈1 buổi ĐO, chưa sửa)

Sau P1/P2, hai khâu này là nghẽn lớn nhất còn lại với video vài tiếng. **Đo trước, không đoán.**

**Giao thức đo (ghi số vào bảng dưới, dùng video thật ≥30ph):**
1. Demucs `separate_no_vocals` ([stem.py:813](backend/pipeline/export/stem.py)):
   phút xử lý / giờ video · VRAM đỉnh · CPU core-giây · cache theo fingerprint đã
   hit khi re-export chưa (đo lần 2 cùng video) · có chạy vô ích khi
   `originalAudioMode=original` không.
2. TTS: giây/câu theo engine (vieneu/edge…) · tỉ lệ cache hit khi re-run ·
   có batch được không.
3. `retime_video_segments`: % project có videoSpeed ≠ 1 · thời gian encode bước này.
4. Toàn trình: bấm giờ từng bước của một export video dài (bảng step→giây) để biết
   thứ tự nghẽn thật sau P1/P2.

**Kết quả đo 2026-07-27** (video thật 519s/145 câu của project 04dcfdeab58e + clip 30s):

| Khâu | Số đo | Ngoại suy /giờ video | Cache lần 2 |
|---|---|---|---|
| Demucs (`separate_no_vocals`) | 70,2s / 519s · VRAM đỉnh ~1,4GB | **~8 phút** | 0,00s (hit `no_vocals_{key}.wav`) |
| TTS VieNeu | nạp model 81s (1 lần) + **3,1s/câu** khi nóng | 145 câu/8,6ph ≈ 1000 câu/giờ → **~52 phút** | ~0 (file `tts/{id}.wav` tồn tại là skip) |
| retime (`retime_video_segments`) | no-op **0,05s** (không videoSpeed → trả nguồn); có 1 câu videoSpeed: 2,3s/30s | ~4,7 phút (re-encode toàn bộ) | — |
| burn (P1/P2) | đã đo ở trên | ~7–10 phút | — |

**Kết luận:** sau P1/P2/P1.5, **TTS là nghẽn số 1** cho lần chạy đầu (~52ph/giờ video,
tuần tự 1 câu/lượt); Demucs (~8ph) và burn (~7–10ph) cùng hạng và đều cache tốt;
re-export gần miễn phí ở mọi khâu. Chọn đúng 2 việc:
1. **TTS song song có kiểm soát** — chạy 2–3 câu cùng lúc (VieNeu ~1,4GB VRAM/stream
   ước tính, GTX 1660 6GB còn chỗ); phải đo VRAM thật trước khi chốt số luồng,
   giữ thứ tự ghi file + cancel_check. Kỳ vọng ~2–3× → ~20ph/giờ video.
2. **retime copy đoạn trống** — tái dùng hạ tầng segment P2: chỉ re-encode span có
   videoSpeed≠1, copy phần còn lại (video 1 giờ chỉnh 5 câu: 4,7ph → <1ph).
KHÔNG sửa Demucs (đã đủ nhanh + cache chuẩn, skip đúng khi mode=original).

## P4 — Frontend: bundle + hoàn tất tách file (≈1 buổi)

Hiện trạng: bundle >600 KB (cảnh báo vite);
[LivePreviewEditor.tsx](frontend/src/features/editor/LivePreviewEditor.tsx) còn 6435 dòng,
[TtsStudio.tsx](frontend/src/features/tts/TtsStudio.tsx) 1997 dòng (đã tách hook/panel đợt 1).

1. `vite.config` `build.rollupOptions.output.manualChunks` hoặc dynamic
   `import()` cho 2 màn nặng: editor (LivePreviewEditor + lib đo caption) và TtsStudio —
   route/tab nào mở mới tải chunk đó.
2. LivePreviewEditor tách tiếp theo seam đã khảo sát: phần render timeline canvas,
   phần overlay caption editor, phần transport (play/seek) — mỗi phần một component
   nhận props hẹp; hook đã tách (useSpeedTransaction/useDubAudioSync/useTimelineDrag)
   giữ nguyên.
3. Sau tách: `tsc --noEmit` sạch + self-check FE (`__checkOcrOverlayLayout`,
   `__checkExportBakePlacement`) + xuất thử 1 preview so WYSIWYG.
- DoD: build hết cảnh báo chunk; số dòng LivePreviewEditor ≤ ~3000; tsc sạch.

## P5 — Vệ sinh (xen kẽ lúc chờ)

1. Test môi trường đỏ → `pytest.mark.skipif` theo điều kiện thật (thiếu fastapi ở
   Python hệ thống, không GPU, không mạng) để `pytest -q` trần luôn xanh, bỏ được
   danh sách `--ignore` dài: test_setup_gate, test_ai_runtime_setup,
   test_adaptive_workers_gpu, test_bundled_caption_fonts, test_logo_overlay,
   test_rendered_videos, test_reveal_output_path, test_tts_studio_schema,
   test_vieneu_frozen_backend, test_vieneu_backend.
2. `backend/app.log` đang **tracked và luôn modified** — `git rm --cached backend/app.log`
   + xác nhận `.gitignore` đã chặn (`*.log`); rà luôn `*.bk*` không lọt staging.
3. Git ref từng bị ghi rỗng (2026-07-27): nếu tái diễn — `.git/logs/HEAD` lấy sha cuối,
   `git update-ref refs/heads/main <sha>`. Bật `git config core.fsyncMethod=fsync`
   (máy này từng mất ref khi crash).
4. Dọn scratchpad bench script còn giá trị → chuyển vào `backend/tools/` nếu tái dùng
   (bench burn, bench OCR); còn lại bỏ.

---

## Việc đã xong (giữ làm hồ sơ)

**P0** — merge `fix/ocr-gpu-decode` + 3 nhánh worktree về main; conflict `locate.py`
giải theo logic main mới (NVDEC batch, 1 chế độ OCR), áp cấu trúc tách file của nhánh.

**P1** (`e75027c`) — «Python chuẩn bị, ffmpeg vẽ»: giữ nguyên phần chuẩn bị
cue/layout/RGBA trong `burn_parts/pipeline.py` (chỗ bảo đảm WYSIWYG), file mới
`burn_parts/ffgraph.py` sinh `filter_complex_script`: glass blur =
crop→scale down→gblur→scale up→eq saturation→overlay; solid = drawbox; mosaic =
pixelize+gblur; chữ = movie PNG + overlay, mỗi cue `enable='between(t,a,b)'`.
Không `-hwaccel` (filter CPU làm hwaccel phản tác dụng). Fallback legacy khi:
logo fade/opacity, nhãn dọc đè nguồn, >160 cue, `VIDEO_CLONE_LEGACY_BURN=1`, ffmpeg lỗi.
Parity gate: `tests/export/test_ffgraph_burn.py` (chạy bằng `./.venv/Scripts/python.exe`).

**P2** (`f3143f7`) — `_keyframe_times` (ffprobe header) → `_plan_segments`
(đệm 0,2s, gộp khoảng <1s, căn keyframe; guard: tiết kiệm ≥5s, ≤40 đoạn, coverage <70%,
nguồn ≥3 keyframe) → cắt segment muxer packet-chính-xác → đoạn active render graph P1
(t dịch về 0), đoạn trống giữ packet gốc → concat demuxer → mux audio một lần cuối.
Lỗi bất kỳ → full graph → legacy.

**Giới hạn ffmpeg đã đo:** expr parser chết ở ~100 nhánh `eq()` → mọi chỗ dùng
`select=` phải chia lô ≤48; graph burn chia lô ≤40 cue/lệnh.

## Thứ tự & ước lượng còn lại

| Bước | Ăn được gì | Effort |
|---|---|---|
| P1.5 | Export đổi khung/resolution: 1 lần encode thay vì 2 (~30–40%); logo hết fallback | 1 buổi |
| P3 | Số liệu Demucs/TTS/retime → quyết 2 việc tiếp | 1 buổi đo |
| P4 | Bundle nhẹ, editor dễ sửa tiếp | 1 buổi |
| P5 | Suite xanh trần, repo sạch, chống mất ref | xen kẽ |

Rủi ro chính: (1) P1.5 crop đặt sai chỗ trong graph → lệch WYSIWYG — gate bằng test
so khung với legacy; (2) segmented+crop làm guard "đáng làm" sai → đo lại ngưỡng;
(3) logo fade khó khớp pixel legacy — nếu lệch quá ngưỡng thì giữ fallback, không ép.
