from clipwright.animation.prompt_skills import compose_skills, select_skills


def test_flow_skills_include_arrow_and_core() -> None:
    body = compose_skills(category="flow", shot_type="flow")
    assert "n-1" in body
    assert "bbox" in body


def test_text_skills_do_not_include_flow_arrow() -> None:
    body = compose_skills(category="text", shot_type="quote")
    assert "text_align" in body
    assert "n-1" not in body
