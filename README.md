# VideoClone

Ứng dụng desktop/web để tải video, nhận diện lời nói hoặc chữ trên khung hình, dịch, lồng tiếng AI và xuất video hoàn chỉnh. Luồng xử lý mặc định chạy local; chỉ các engine cloud đã chọn mới gửi dữ liệu ra ngoài.

## Chức năng

| Màn hình | Nội dung |
|---|---|
| **Clone Video** | Upload video → Whisper/OCR → dịch → sửa từng đoạn → lồng tiếng → xuất MP4 |
| **Đã render** | Xem thumbnail, đổi tên, phát, tải xuống và mở thư mục; 10 video mỗi trang, mới nhất trước |
| **Download Video** | Tải video từ URL, quản lý hàng đợi và đưa file đã tải vào Clone Video |
| **Text to Speech** | Tạo audio từ văn bản, clone/quản lý giọng, xuất WAV/MP3/SRT/ZIP và xem lịch sử |
| **Cấu hình** | API key, engine, thư mục dữ liệu và kiểm tra/cài các thành phần bắt buộc |

### Pipeline Clone Video

1. Nhận diện lời nói bằng `faster-whisper` hoặc chữ trên video bằng RapidOCR.
2. Dịch bằng Google, TikTok, MyMemory, Ollama hoặc dịch vụ có API key.
3. Sửa nội dung, thời gian và loại đoạn trong editor (`horizontal`, `vertical`, `label`).
4. Tạo giọng đọc, điều chỉnh tốc độ, âm lượng và cao độ.
5. Dùng FFmpeg để che hardsub gốc, burn bản dịch, ghép audio và xuất MP4.

Mỗi project giữ output mới nhất. Các output cũ hợp lệ vẫn được quét vào tab **Đã render** và tự tạo/cache thumbnail khi cần.

## Engine hỗ trợ

### Dịch

| Engine | Ghi chú |
|---|---|
| Google · TikTok · MyMemory | Không cần API key; fallback mặc định Google → TikTok → MyMemory |
| Ollama | LLM local |
| OpenAI · Gemini · DeepSeek · OpenRouter · Grok | Cần API key trong **Cấu hình** hoặc biến môi trường |

### TTS

| Engine | Ghi chú |
|---|---|
| **zmAI** | Bộ giọng tham chiếu tiếng Việt đi kèm, chạy qua VieNeu |
| **VieNeu Local** | TTS tiếng Việt local, hỗ trợ giọng có sẵn và giọng clone |
| **CapCut** | Dịch vụ không chính thức, không bảo đảm SLA |
| **ElevenLabs** | TTS cloud; hỗ trợ nhiều key qua `ELEVENLABS_API_KEYS` |
| **System** | Giọng hệ điều hành: macOS `say`, Linux `espeak-ng` và Windows SAPI khi khả dụng |

VieNeu/Whisper/OCR là nhóm AI nặng. Bản desktop không đóng gói sẵn nhóm này: lần mở đầu tiên, vào **Cấu hình → Thiết lập** và cài xong gói bắt buộc trước khi sử dụng.

## Chạy từ source

### Yêu cầu

- Node.js 18+
- Python 3.11+ (khuyến nghị 3.12)
- FFmpeg và FFprobe trên `PATH`
- Windows, macOS hoặc Linux

### Cài đặt và chạy

```bash
npm run setup
npm run dev:all
```

`setup` cài package frontend, tạo `backend/.venv` và cài `backend/requirements.txt`. `dev:all` mở đồng thời:

- UI: `http://127.0.0.1:5173`
- API: `http://127.0.0.1:8787`
- Swagger: `http://127.0.0.1:8787/docs`

Các lệnh riêng:

```bash
npm run dev       # chỉ Vite
npm run server    # chỉ FastAPI
npm run build     # kiểm tra TypeScript và build frontend
```

Sao chép `backend/.env.example` thành `backend/.env` nếu cần cấu hình qua file:

```env
ELEVENLABS_API_KEYS=sk_xxx,sk_yyy
# OPENAI_API_KEY=
# GEMINI_API_KEY=
# CAPCUT_DEVICE_JSON=
```

## Build ứng dụng Windows

Chuẩn bị môi trường build theo `.github/workflows/release-windows.yml`, sau đó chạy:

```bash
npm run build:app
```

Bản mặc định là thư mục chạy độc lập tại `build_app/release/VideoClone_v<version>/`. Phải giữ nguyên cả thư mục, không sao chép riêng `VideoClone.exe`.

```powershell
$env:ONEFILE='1'; npm run build:app   # tuỳ chọn: một file EXE, build/chạy đầu chậm hơn
$env:CLEAN='1'; npm run build:app     # xoá cache PyInstaller của lần build trước
```

GitHub Actions tự build artifact Windows khi chạy thủ công hoặc push tag `v*`; với tag, workflow đồng thời tạo/cập nhật GitHub Release gồm EXE và ZIP.

## Dữ liệu

- Source mode: `backend/public/<project_id>/` chứa media project/output.
- Giọng clone: `backend/data/voices/vieneu/cloned/`.
- Desktop Windows: dữ liệu và package AI cài sau nằm trong `%LOCALAPPDATA%\VideoClone`.

Không xoá các thư mục dữ liệu nếu còn project, bản render hoặc giọng clone cần giữ.

## API chính

Base URL: `http://127.0.0.1:8787`

| API | Chức năng |
|---|---|
| `POST /api/upload` | Tạo project từ video |
| `POST /api/projects/{id}/run` | Nhận diện và dịch |
| `POST /api/projects/{id}/dub` | Tạo audio lồng tiếng |
| `POST /api/projects/{id}/export` | Xuất video |
| `GET /api/projects/{id}/segments` | Đọc danh sách đoạn |
| `PUT /api/projects/{id}/segments/{segment_id}` | Sửa một đoạn |
| `GET /api/renders` | Danh sách video đã render |
| `POST /api/tts/studio/synthesize` | Tạo audio trong TTS Studio |
| `GET /api/system/checks` | Kiểm tra thành phần hệ thống |

Danh sách đầy đủ và schema request/response có tại `/docs` khi API đang chạy.

## Cấu trúc source

```text
frontend/src/
  app/          shell, mode và session
  pages/        trang cấp cao
  features/     editor, project, download, TTS, cấu hình
  shared/       API client, component và type dùng chung

backend/
  main.py       entry point FastAPI
  api/routes/   route HTTP theo domain
  pipeline/     ASR, OCR, dịch, TTS, download, export và điều phối job
  tests/        kiểm thử backend

scripts/        setup và dev runner
build_app/      launcher, cấu hình và output desktop
```

Quy tắc đặt file và hướng dẫn chia module: [STRUCTURE.MD](STRUCTURE.MD).

## Kiểm tra trước khi cập nhật

```bash
npm run build
backend/.venv/Scripts/python.exe -m pytest backend/tests
```

Trên macOS/Linux, thay đường dẫn Python bằng `backend/.venv/bin/python`.

## Giới hạn

- Kết quả OCR phụ thuộc độ rõ, vị trí và hiệu ứng chữ trong video.
- Whisper model lớn và một số backend VieNeu cần nhiều RAM/GPU; model nhỏ hoặc ONNX vẫn có thể chạy CPU.
- CapCut, Google, TikTok và MyMemory là dịch vụ ngoài/không SLA, có thể thay đổi hoặc giới hạn tần suất.
- Luôn kiểm tra lại bản dịch, timing và audio trước khi xuất bản cuối.

## License

Private / nội bộ — điều chỉnh theo chính sách của repository.
