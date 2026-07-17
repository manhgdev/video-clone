# Video-Clone

Studio **dịch phụ đề + lồng tiếng AI** cho video (đặc biệt short / nấu ăn có hardsub CJK).

- Nhận diện lời nói (Whisper) **hoặc** chữ trên màn hình (OCR)
- Dịch sang tiếng Việt (và ngôn ngữ khác)
- TTS lồng tiếng (CapCut / ElevenLabs / hệ thống)
- **Cover** hardsub gốc + **burn** bản dịch (horizontal / title dọc / nhãn graphic)

Mặc định chạy **local**; cloud chỉ khi bật engine / có API key.

---

## Tính năng chính

| Bước | Chi tiết |
|------|----------|
| **ASR** | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — voice |
| **OCR** | RapidOCR — hardsub đáy, title dọc, nhãn nguyên liệu / 1 chữ giữa khung |
| **Dịch** | Google free · MyMemory · TikTok free · Ollama · OpenAI / Gemini / DeepSeek / OpenRouter / Grok |
| **TTS** | CapCut (unofficial) · ElevenLabs · macOS `say` / Linux `espeak-ng` |
| **Export** | ffmpeg: cover hardsub + burn VI + mux audio |

### Layout đoạn

- **horizontal** — phụ đề đáy (TTS mặc định bật)
- **vertical** — tiêu đề dọc đầu clip (TTS mặc định tắt; có checkbox **Lồng tiếng**)
- **label** — nhãn graphic / nguyên liệu (TTS mặc định tắt; cover bám OCR, chữ VI fit ô)

### OCR phụ (engine `paddleocr` / `screen`)

1. Hardsub đáy (frames cache)  
2. Hardsub ngắn giữa khung (vd. `行`)  
3. Title dọc  
4. Nhãn cột bên / nguyên liệu (song song, coarse + refine)

---

## Yêu cầu

- **Node** 18+ (Vite + React)
- **Python** 3.11+ (khuyến nghị 3.12+)
- **ffmpeg** trên PATH
- macOS / Linux (TTS hệ thống khác nhau)

Tuỳ chọn:

- [Ollama](https://ollama.com) — dịch local  
- `ELEVENLABS_API_KEYS` — TTS cloud  
- Key cloud dịch (OpenAI, Gemini, …) trong UI **Cấu hình** hoặc `.env`

---

## Cài & chạy

```bash
# Frontend
npm install

# Backend
cd server
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# (tuỳ chọn) Ollama
ollama serve
ollama pull llama3.2:1b
```

Hai terminal:

```bash
# API — http://127.0.0.1:8787
cd server && source .venv/bin/activate
python -m uvicorn main:app --reload --port 8787

# UI — http://localhost:5173
npm run dev
```

Hoặc:

```bash
npm run server   # chỉ backend
```

Env mẫu: `backend/.env.example` → `backend/.env`

```env
ELEVENLABS_API_KEYS=sk_xxx,sk_yyy
# CAPCUT_DEVICE_JSON=/path/to/device.json
# OPENAI_API_KEY=
# GEMINI_API_KEY=
```

---

## Workflow UI

1. **Upload / chọn video** (sidebar)  
2. Chọn engine: Whisper / OCR màn hình; ngôn ngữ nguồn–đích; translator; giọng TTS  
3. **Dịch toàn bộ** → ASR/OCR + dịch  
4. Sửa segment (thời gian, bản dịch, **Lồng tiếng** cho dọc/nhãn)  
5. **Lồng tiếng** → TTS  
6. **Xuất bản** → cover + burn + mux → tải MP4  

Dữ liệu project (public media): `backend/public/<project_id>/`.
Clone voices (private): `backend/data/voices/vieneu/cloned/`.

---

## Cấu trúc repo

```
frontend/
  src/                    React UI (app, pages, features, shared)
  public/                 static frontend (favicon, logo)
  index.html
  vite.config.ts
backend/
  main.py                 FastAPI
  requirements.txt
  data/                   private (clone voices, app_config, temp)
  public/                 video/audio jobs
  resources/voice-ref/    zmAI reference wav
  pipeline/
    asr/ ocr/ tts/ export/ core/ run.py
  tests/
scripts/                  setup + dev:all
```

---

## Dịch (translator)

| Engine | Ghi chú |
|--------|---------|
| `google` | Free GTX API — không key |
| `mymemory` | Free, quota theo IP |
| `tiktok` | Free content translate |
| `ollama` | Local LLM |
| `openai` / `gemini` / `deepseek` / `openrouter` / `grok` | Cần key |

Free chain fallback: **Google → TikTok → MyMemory**.

Clean bản dịch: bỏ CJK sót, chuẩn hoá `·` / `、` → `, ` (giữ list nguyên liệu).

---

## TTS

| Engine | Ghi chú |
|--------|---------|
| **CapCut** | Unofficial ([K07VN/capcut-tts-api](https://github.com/K07VN/capcut-tts-api)); `capcut_device.json` tự tạo; shark `ret=-6` → xóa file để mint lại |
| **ElevenLabs** | `ELEVENLABS_API_KEYS` (xoay key khi 401/429) |
| **system** | macOS `say` / `espeak-ng` |

Giọng mặc định UI: CapCut **Thanh Niên Tự Tin** (`cc:BV075_streaming:…`).

Title dọc / nhãn: **không TTS** trừ khi bật checkbox **Lồng tiếng** trên card.

---

## Export (cover + burn)

- Hardsub đáy: dải dưới, blur/cover + burn VI  
- Title dọc: cột giữa, font VI đủ dấu  
- Nhãn: cover bám OCR (multi-box), chữ fit ô (stack dọc nếu cột CJK)  
- Cache OCR boxes theo version (`ocr_boxes_v*`); đổi logic → version mới (export lại)

---

## API (tóm tắt)

Base: `http://127.0.0.1:8787`

- `POST /projects` — upload video  
- `GET /projects` · `GET /projects/{id}`  
- `POST /projects/{id}/run` — asr / translate / tts / export  
- `PATCH /projects/{id}/segments/{seg_id}` — sửa segment (`layout`, `dub`, …)  
- `GET /projects/{id}/export` — file xuất  

Chi tiết: mở `/docs` khi uvicorn chạy.

---

## Ghi chú / giới hạn

- OCR phụ đề phụ thuộc chất lượng chữ trên khung; cache ASR version (`o*`) — đổi engine OCR cần chạy lại **Dịch toàn bộ**  
- CapCut TTS không chính thức, có thể bị rate-limit  
- Google/MyMemory free: không SLA; list nhãn nên kiểm tra dấu phẩy sau clean  
- Cần GPU cho Whisper lớn; CPU vẫn chạy được model nhỏ  

---

## License

Private / nội bộ — chỉnh theo nhu cầu repo.
