# VideoClone

Ứng dụng desktop/web: tải video, nhận diện lời/chữ, dịch, lồng tiếng AI và xuất MP4. Xử lý mặc định **local**; chỉ engine cloud đã chọn mới gửi dữ liệu ra ngoài.

**Version hiện tại:** xem `package.json` (desktop build: `npm run build:app`).

---

## Chức năng chính

| Màn hình | Nội dung |
|---|---|
| **Clone Video** | Upload → ASR/OCR → dịch → sửa timeline/bbox → lồng tiếng → xuất MP4 |
| **Đã render** | Thumbnail, đổi tên, phát, tải, mở thư mục |
| **Download Video** | Tải từ URL, hàng đợi, đưa vào Clone |
| **Text to Speech** | Văn bản/SRT → audio, clone giọng, WAV/MP3/SRT/ZIP |
| **Cấu hình** | API key, engine, thư mục dữ liệu, kiểm tra/cài gói AI |

### Pipeline Clone Video

1. **ASR** `faster-whisper` (CUDA khi có) hoặc **OCR** RapidOCR (CUDA khi `onnxruntime-gpu`).
2. **Dịch** Google / TikTok / MyMemory / Ollama / cloud có key.
3. **Editor** sửa text, timing, layout (`horizontal` / `mid` / `vertical` / `label`), bbox, tốc độ video bake.
4. **TTS** VieNeu (zmAI/clone), CapCut, ElevenLabs, System.
5. **Export** FFmpeg: che hardsub, burn caption, mux TTS + BGM, tách stem (Demucs).

Project giữ output mới nhất; tab **Đã render** quét các bản xuất hợp lệ.

---

## Engine

### Dịch

| Engine | Ghi chú |
|---|---|
| Google · TikTok · MyMemory | Không key; fallback Google → TikTok → MyMemory |
| Ollama | LLM local |
| OpenAI · Gemini · DeepSeek · OpenRouter · Grok | Key trong **Cấu hình** hoặc `.env` |

### TTS

| Engine | Ghi chú |
|---|---|
| **zmAI** | Giọng tham chiếu VI đi kèm (VieNeu) |
| **VieNeu Local** | TTS VI local + clone; ưu tiên **CUDA / Apple MPS** |
| **CapCut** | Cloud không chính thức, không SLA |
| **ElevenLabs** | Cloud; nhiều key qua `ELEVENLABS_API_KEYS` |
| **System** | macOS `say` / Linux `espeak-ng` / Windows SAPI |

### GPU (local AI)

| Thành phần | Ưu tiên |
|---|---|
| VieNeu | CUDA → MPS → ONNX/CPU |
| Whisper | CUDA (ctranslate2) → CPU |
| OCR | CUDA (onnxruntime-gpu) → CPU |
| Demucs stem | CUDA / MLX (Apple) → CPU |
| FFmpeg encode | NVENC khi có |

Desktop **không** đóng gói sẵn torch/OCR nặng: lần đầu vào **Cấu hình → Thiết lập** cài gói bắt buộc. Runtime Windows: `%LOCALAPPDATA%\VideoClone\.venv-runtime` (và `.venv-ocr`).

**Huỷ job:** nút **Huỷ** (đỏ) / **X** khi đang chạy → cancel + kill process tree (ffmpeg/TTS/OCR). **Chạy nền** chỉ thu nhỏ popup, job vẫn chạy.

---

## Chạy từ source

### Yêu cầu

- Node.js 18+ qua [NVM](https://github.com/nvm-sh/nvm) (Windows: [NVM for Windows](https://github.com/coreybutler/nvm-windows))
- Python 3.10–3.12 (khuyến nghị 3.12; script setup tự cài 3.12 bằng Homebrew/winget nếu thiếu)
- FFmpeg + FFprobe trên `PATH`
- Windows / macOS / Linux

### Cài & dev

```bash
npm run setup
npm run dev:all
```

| URL | |
|---|---|
| UI | http://127.0.0.1:5173 |
| API | http://127.0.0.1:8787 |
| Swagger | http://127.0.0.1:8787/docs |

```bash
npm run dev        # Vite
npm run server     # FastAPI :8787
npm run build      # tsc + vite build
npm run build:app  # desktop PyInstaller
```

Sao chép `backend/.env.example` → `backend/.env` nếu cần:

```env
ELEVENLABS_API_KEYS=sk_xxx,sk_yyy
# OPENAI_API_KEY=
# GEMINI_API_KEY=
# VIENEU_BACKEND=auto          # auto | pytorch | onnx | cpu
# VIDEOCLONE_TORCH_DEVICE=     # cuda | mps | cpu (debug)
```

---

## Build desktop Windows

Theo `.github/workflows/release-windows.yml`:

```bash
npm run build:app
```

Output: `build_app/release/VideoClone_v<version>/` — **chạy cả thư mục**, không copy riêng `.exe`.

### Quy tắc tăng version

VideoClone dùng ba số `major.minor.patch`, mỗi số chạy từ `0` đến `9`:

- Build thường tăng patch: `3.0.0 → 3.0.1`.
- Patch `9` cuộn sang minor: `3.0.9 → 3.1.0`.
- Minor `9` và patch `9` cuộn sang major: `2.9.9 → 3.0.0`.
- `npm run build:app` lấy version hiện tại từ `package.json`, ghi cùng version vào tiêu đề APP, `build_app/VERSION`, tên thư mục và ZIP; chỉ sau khi build thành công mới ghi version kế tiếp vào `package.json`.
- Script build có self-check bắt buộc cho các mốc `2.0.9 → 2.1.0` và `2.9.9 → 3.0.0`; sai quy tắc thì build dừng.

```powershell
$env:ONEFILE='1'; npm run build:app   # optional one-file
$env:CLEAN='1'; npm run build:app     # xóa cache PyInstaller
```

GitHub Actions build artifact khi workflow thủ công hoặc tag `v*` (Release kèm ZIP).

---

## Dữ liệu

| Mode | Đường dẫn |
|---|---|
| Source | `backend/public/<project_id>/` |
| Giọng clone | `backend/data/voices/vieneu/` |
| Desktop Win | `%LOCALAPPDATA%\VideoClone` (data + AI packages) |

Không xóa thư mục data nếu còn project/render/giọng cần giữ.

---

## API chính

Base: `http://127.0.0.1:8787`

| Method | Path | |
|---|---|---|
| POST | `/api/upload` | Tạo project |
| POST | `/api/projects/{id}/run` | ASR + dịch |
| POST | `/api/projects/{id}/dub` | Lồng tiếng |
| POST | `/api/projects/{id}/export` | Xuất video |
| POST | `/api/projects/{id}/cancel` | Huỷ job (kill process) |
| POST | `/api/projects/{id}/status/dismiss` | Đóng popup lỗi (clear meta) |
| GET | `/api/projects/{id}/status` | Tiến độ |
| GET/PUT | `/api/projects/{id}/segments` | Danh sách / thay segments |
| GET | `/api/renders` | Video đã render |
| POST | `/api/tts/studio/synthesize` | TTS Studio |
| GET | `/api/system/checks` | Kiểm tra hệ thống |

Schema đầy đủ: `/docs` khi API chạy.

---

## Cấu trúc source (tóm tắt)

```text
frontend/src/   app · pages · features · shared
backend/        api/routes · pipeline
tests/          backend tests · frontend tests
scripts/        setup · dev
build_app/      launcher · PyInstaller · release
```

Chi tiết module và quy tắc phụ thuộc: **[STRUCTURE.md](STRUCTURE.md)**.

---

## Kiểm tra trước khi merge

```bash
npm run build
backend/.venv/Scripts/python.exe -m pytest tests/backend
```

macOS/Linux: `backend/.venv/bin/python -m pytest tests/backend`.

---

## Giới hạn

- OCR phụ thuộc độ rõ / vị trí chữ trên khung hình.
- Whisper lớn và VieNeu PyTorch cần RAM/VRAM; máy yếu dùng model nhỏ / ONNX.
- CapCut, Google, TikTok, MyMemory: dịch vụ ngoài, có thể đổi hoặc rate-limit.
- Luôn rà bản dịch, timing và audio trước xuất bản cuối.

## License

Private / nội bộ — theo chính sách repository.
