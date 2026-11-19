from clipwright.animation.qa import qa_motion, qa_report, qa_text, read_seconds


def test_read_seconds_chinese() -> None:
    assert 2.0 <= read_seconds("让每一帧都有信息量") <= 5.0


def test_qa_text_flags_small_and_long() -> None:
    mg = {"width": 1920, "height": 1080, "duration_sec": 1.0, "params": {}, "elements": [
        {"type": "text", "content": "这是一个超过二十四字的非常长的文字段落需要拆句处理否则手机上看不清", "font_size": 20, "keyframes": [{"time": 0, "opacity": 0}, {"time": 1, "opacity": 1}]},
    ]}
    issues = qa_text(mg)
    assert any("4%" in x for x in issues)
    assert any("超过 24" in x for x in issues)


def test_qa_motion_flags_static_and_dense() -> None:
    mg = {"elements": [{"type": "text", "content": "A"}] + [{"type": "shape", "keyframes": [{"time": 0}]} for _ in range(6)]}
    issues = qa_motion(mg)
    assert any("无关键帧" in x for x in issues)
    assert any("> 6" in x for x in issues)


def test_qa_report_ok() -> None:
    mg = {"width": 1920, "height": 1080, "duration_sec": 4, "params": {}, "elements": [
        {"type": "text", "content": "标题", "font_size": 96, "keyframes": [{"time": 0, "opacity": 0}, {"time": 1, "opacity": 1}]},
    ]}
    assert qa_report(mg)["ok"] is True
