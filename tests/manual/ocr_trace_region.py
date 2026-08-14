import os
import sys
from pathlib import Path

os.environ['PYTHONIOENCODING'] = 'utf-8'
ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
import pipeline.ocr.extract_parts.api as api

video = ROOT / 'tests' / 'backend' / 'video' / 'ocr_9_16_chanh_30.mp4'
print('Running OCR pipeline with analysisRegion...')
region = {'x': 0.0, 'y': 0.4, 'w': 1.0, 'h': 0.2}
segs = api.asr_paddleocr_inprocess(video, project_id='trace_hooks3', reuse_frames=False, workers=1, analysis_region=region)

print('\nFinal segments:')
for s in segs:
    print(f"  [{s.get('layout', 'none')}] {s.get('start'):.2f}-{s.get('end'):.2f}s: '{s.get('source')}' maskOnly={s.get('maskOnly')}")
