"""B12: _build_input("audio") 透传前端 voice_id / auto_dub。

修复前 audio_config 硬编码 auto_dub=True、voice_id 仅取 persona。
"""

from __future__ import annotations

from clipwright.services.pipeline_v2 import PipelineOrchestratorV2


def _orchestrator() -> PipelineOrchestratorV2:
    # __new__ 跳过 _agents 构造（避免实例化全部 Agent 的额外开销）
    return PipelineOrchestratorV2.__new__(PipelineOrchestratorV2)


def _audio_config(**extra):
    orch = _orchestrator()
    persona_config = {"audio": {"voice_clone_model_id": "persona_voice", "voice": "persona_voice2"}}
    inputs = orch._build_input("audio", {"timeline": {"id": "tl", "tracks": []}}, persona_config, None, extra_params=extra)
    return inputs["audio_config"]


class TestAudioConfigPassthrough:
    def test_voice_id_from_extra_params_wins(self) -> None:
        cfg = _audio_config(voice_id="v_front")
        assert cfg["voice_id"] == "v_front"

    def test_auto_dub_false_from_extra_params(self) -> None:
        cfg = _audio_config(auto_dub=False)
        assert cfg["auto_dub"] is False

    def test_voice_id_and_auto_dub_together(self) -> None:
        cfg = _audio_config(voice_id="v_front", auto_dub=False)
        assert cfg["voice_id"] == "v_front"
        assert cfg["auto_dub"] is False

    def test_default_voice_id_falls_back_to_persona(self) -> None:
        cfg = _audio_config()
        assert cfg["voice_id"] == "persona_voice"

    def test_default_auto_dub_is_true(self) -> None:
        cfg = _audio_config()
        assert cfg["auto_dub"] is True

    def test_persona_config_still_merged(self) -> None:
        cfg = _audio_config()
        # persona audio 其它字段仍保留
        assert "voice_clone_model_id" in cfg
