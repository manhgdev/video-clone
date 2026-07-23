import sys, os
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, r'd:\DEV\video-clone\backend')
from pathlib import Path
from rapidocr_onnxruntime import RapidOCR
from pipeline.ocr.extract_parts.runtime import _rapidocr_gpu_kwargs
import cv2

video = Path(r'd:\DEV\video-clone\backend\tests\video\ocr_9_16_chanh_30.mp4')
# extract 1 frame
frame_path = video.parent / "test_frame.jpg"
os.system(f"ffmpeg -y -i {str(video)} -vf fps=1 -vframes 1 {str(frame_path)}")

img = cv2.imread(str(frame_path))
h, w = img.shape[:2]
_fvh = h

ocr = RapidOCR(**_rapidocr_gpu_kwargs())
result, _ = ocr(str(frame_path))

bottom_lines, mid_lines, vert_lines = [], [], []

if result:
    for row in result:
        box = row[0]
        text = str(row[1]).strip()
        ys = [float(p[1]) for p in box]
        xs = [float(p[0]) for p in box]
        ncy = (min(ys) + max(ys)) / 2.0 / _fvh
        bh = max(ys) - min(ys)
        bw = max(xs) - min(xs)
        
        is_vert = (bw > 0 and bh > bw * 1.5)
        portrait = h > w > 0
        band = 0.18 if portrait else 0.22
        y0 = 1.0 - band
        
        if is_vert:
            vert_lines.append(text)
        elif ncy >= y0:
            bottom_lines.append(text)
        else:
            mid_lines.append(text)

print("BOTTOM:", bottom_lines)
print("MID:", mid_lines)
print("VERT:", vert_lines)
