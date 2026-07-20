# Cấu trúc source

## Frontend

```text
frontend/src/
├─ app/
│  ├─ App.tsx              Shell và điều phối mode
│  ├─ appMode.ts           Mode/tab cấp cao
│  ├─ appSettings.ts       Settings toàn app
│  └─ useProjectSession.ts Session project
├─ pages/                  Một component cho mỗi trang cấp cao
├─ features/
│  ├─ configuration/       Cấu hình engine/API
│  ├─ download/            Form, job và UI download
│  ├─ editor/              Preview editor, timeline và helper editor
│  ├─ project/             Project, segment, sidebar và pipeline step
│  └─ tts/                 TTS Studio, clone voice, history và settings
└─ shared/
   ├─ api/                 HTTP helper dùng chung
   ├─ components/          Component dùng ở nhiều feature
   ├─ lib/                 Helper thuần dùng chung
   ├─ types/               Type dùng chung
   └─ ui/                  UI primitive
```

### Chia frontend tiếp

- `App.tsx`: chỉ giữ state/liên kết cấp ứng dụng. Luồng riêng dài nên chuyển vào hook hoặc feature đang sở hữu.
- `LivePreviewEditor.tsx` và `TtsStudio.tsx`: tách từng panel hoàn chỉnh khi panel có state + hành vi riêng; helper tính toán thuần đặt trong `lib/` của feature.
- API và type thuộc một domain đặt cạnh domain: `project.api.ts`, `project.types.ts`.
- Không thêm code mới vào `frontend/src/components`, `lib`, `services`; đây là lớp cũ. Khi chạm tới, chuyển sang `shared/` hoặc feature phù hợp nếu diff nhỏ và an toàn.
- Không tạo wrapper/placeholder chỉ để “chuẩn cấu trúc”. Component được tách phải giảm kích thước hoặc cô lập một hành vi thật.

## Backend

```text
backend/
├─ api/
│  ├─ app.py               Tạo FastAPI app
│  ├─ deps.py              Pydantic schema và validation dùng chung
│  ├─ routes_all.py        Đăng ký router
│  └─ routes/              Route theo domain
├─ pipeline/
│  ├─ asr/                 Nhận dạng tiếng nói
│  ├─ ocr/                 Nhận dạng chữ và vị trí chữ
│  ├─ mt/                  Dịch máy
│  ├─ tts/                 Engine, voice store và TTS Studio
│  ├─ export/              Burn, mux, crop và encode
│  ├─ download/            Download job
│  ├─ orchestrate/         Điều phối job nhiều bước
│  └─ core/                Project, media, resource, job và config dùng chung
└─ tests/                  Test theo domain/hành vi
```

### Chia backend tiếp

- `api/routes/<domain>.py`: parse/validate request, gọi pipeline, đổi lỗi thành HTTP response.
- `pipeline/<domain>/`: chứa nghiệp vụ; không import FastAPI tại đây.
- `orchestrate/`: chỉ dành cho luồng phối hợp từ hai domain trở lên.
- `core/`: chỉ nhận helper hạ tầng đã được nhiều domain dùng; không gom helper một lần dùng.
- File lớn nên tách theo bước xử lý có input/output rõ ràng. Giữ API công khai ở `__init__.py` khi việc tách file không nên làm caller đổi import.
- Bug dùng chung phải sửa tại helper/pipeline gốc và thêm một test nhỏ ở `backend/tests/`.

## Quy tắc phụ thuộc

```text
Frontend: app/pages → features → shared
Backend:  api/routes → orchestrate hoặc pipeline domain → core
```

- `shared` và `core` không phụ thuộc ngược vào feature/domain cụ thể.
- Hai feature không import component nội bộ của nhau; đưa phần dùng chung thật sự vào `shared`.
- Route không gọi route khác; gọi chung một hàm pipeline.

## Khi cập nhật cấu trúc

Chỉ cập nhật tài liệu khi thay đổi thư mục, trách nhiệm module hoặc quy tắc phụ thuộc. Không liệt kê mọi file con và không ghi tính năng, cách cài đặt hay API endpoint tại đây.
