import cv2

cap = cv2.VideoCapture(r'server\data\0e5d59df6b5b\source.mp4')
print('FPS:', cap.get(cv2.CAP_PROP_FPS))
print('Frames:', cap.get(cv2.CAP_PROP_FRAME_COUNT))
print('W:', cap.get(cv2.CAP_PROP_FRAME_WIDTH), 'H:', cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
for t in [0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 13.0]:
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, frame = cap.read()
    fn = int(cap.get(cv2.CAP_PROP_POS_FRAMES)) if ok else -1
    print(f't={t}s fn={fn} ok={ok}')
cap.release()
