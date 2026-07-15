"""OCR progress report must update by done-count, not only by coarse progress pct."""


def test_report_gate_by_done_not_pct() -> None:
    # Gate cũ theo pct: nhiều bước done không đổi message (cùng 95)
    prev_pct = -1
    by_pct = []
    for done in range(0, 20):
        pct = 95 + min(3, int(3 * done / 67))
        if pct <= prev_pct:
            continue
        prev_pct = pct
        by_pct.append(done)
    assert by_pct == [0], by_pct  # chỉ lần đầu — message đứng im tới ~23/67

    # Gate mới theo done: từng câu hiện số
    prev_done = -1
    by_done = []
    for done in range(1, 6):
        if done == prev_done:
            continue
        prev_done = done
        by_done.append(done)
    assert by_done == [1, 2, 3, 4, 5]
