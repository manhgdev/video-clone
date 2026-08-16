import cv2
from pathlib import Path
from pipeline.ocr.locate import attach_speech_hardsub_boxes_inprocess

CLIP = Path("/Users/manhg/DEV/TOOL/PYTHON/video-clone/backend/public/2ef6a0cdabf8/cache/preview_20.mp4")

segments = [
    {"id": "1", "start": 1.20, "end": 2.84, "source": "三一半 苏越林"},
    {"id": "2", "start": 2.86, "end": 5.36, "source": "决醒SS 几天赴断剑线"},
    {"id": "3", "start": 5.38, "end": 10.66, "source": "莫头 说天了三天的女人"},
    {"id": "4", "start": 10.68, "end": 16.77, "source": "这么大盘战机空降啊"}
]

n = attach_speech_hardsub_boxes_inprocess(CLIP, segments, only_missing=False)
print(f"Located {n} boxes")
for s in segments:
    print(f"[{s['start']:.2f}-{s['end']:.2f}] {s['source']} -> bbox: {s.get('bbox')}")
