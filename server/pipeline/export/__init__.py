"""Export: cover/burn hardsubs + mux dub.

Caption/translate burn layout lives in burn.py (đóng — đừng trộn OCR vào).

OCR (tách riêng):
  pipeline.ocr.extract  — RapidOCR đọc chữ trên màn
  pipeline.ocr.locate   — định vị box lúc xuất
  pipeline.ocr.labels   — layout nhãn / cột

Shims cũ (tương thích import):
  export/labels.py → ocr.labels
  export/ocr_locate.py → ocr.locate
"""
