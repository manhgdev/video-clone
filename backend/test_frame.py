import cv2
import math
from pipeline.ocr.locate import rapidocr_labels

CLIP = "/Users/manhg/DEV/TOOL/PYTHON/video-clone/backend/public/2ef6a0cdabf8/cache/preview_20.mp4"
cap = cv2.VideoCapture(CLIP)
fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
cap.set(cv2.CAP_PROP_POS_FRAMES, int(1.22 * fps))
ret, frame = cap.read()

ocr = rapidocr_labels()
result, _ = ocr(frame)

for row in result or []:
    box, text = row[0], row[1]
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    bx0, by0 = int(min(xs)), int(min(ys))
    bx1, by1 = int(max(xs)), int(max(ys))
    print(f"text='{text}' bbox={{'x': {bx0}, 'y': {by0}, 'w': {bx1-bx0}, 'h': {by1-by0}}}")

cap.release()
