# Video-Clone

Studio dịch thuật & lồng tiếng AI — mặc định local, cloud khi có key/setting.

## Stack

| Bước | Engine |
|------|--------|
| ASR | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| OCR phụ đề | RapidOCR / PaddleOCR (tuỳ chọn) |
| Dịch | [Ollama](https://ollama.com); fallback Google Translate free khi LLM hỏng |
| TTS | macOS `say` / Linux `espeak-ng`; [CapCut](https://github.com/K07VN/capcut-tts-api) (unofficial); ElevenLabs khi có `ELEVENLABS_API_KEYS` |
| Video | ffmpeg |

Cloud (ElevenLabs, Google free fallback, CapCut TTS) chỉ chạy khi cần — không bắt buộc key để dùng local.

CapCut TTS dùng API không chính thức ([K07VN/capcut-tts-api](https://github.com/K07VN/capcut-tts-api)); spam có thể bị chặn (`ret=-6`). Lần đầu server tự ghi `capcut_device.json` (id riêng). Bị shark: xóa file đó để mint lại, hoặc set `CAPCUT_DEVICE_JSON`.

## Chạy

```bash
# 1) Ollama (dịch local)
ollama serve
ollama pull llama3.2:1b

# 2) Backend
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8787

# 3) Frontend
npm install && npm run dev
```

Mở http://localhost:5173

ElevenLabs (tuỳ chọn): copy `server/.env.example` → `server/.env`, điền `ELEVENLABS_API_KEYS`.

## Workflow

1. Chọn video ở sidebar  
2. **Dịch toàn bộ** → ASR + dịch  
3. Sửa bản dịch nếu cần  
4. **Lồng tiếng** → TTS từng đoạn  
5. **Xuất bản** → tải MP4 đã ghép

## Cấu trúc

```
src/                 React UI
  components/        Header, Sidebar, Stepper, Segment*
  services/          api.ts
server/
  main.py            FastAPI (uvicorn main:app)
  tests/             smoke scripts
  pipeline/
    core/            config, jobs, project, media
    asr.py           Whisper + OCR
    translate.py     Ollama + Google fallback
    tts/             CapCut + ElevenLabs + say
    export/          burn hardsub + mux
    run.py           orchestrators
```
