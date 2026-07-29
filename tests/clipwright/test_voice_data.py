"""T3: voice data layer tests — VoiceRecord / VoiceResult / VoiceStorage / split_text."""

import json
import pytest
from pathlib import Path
from clipwright.services.voice import (
    VoiceRecord,
    VoiceResult,
    VoiceStorage,
    split_text,
    _guess_mime,
)


# ──────────────────────────────────────────────
# VoiceRecord
# ──────────────────────────────────────────────


class TestVoiceRecord:
    def test_round_trip(self):
        rec = VoiceRecord(
            id="abc123",
            provider="qwen-tts",
            voice_id="v_123",
            voice_name="my_voice",
            target_model="qwen3-tts-vc",
            created_at="2026-07-21T10:00:00",
        )
        d = rec.model_dump()
        rec2 = VoiceRecord.model_validate(d)
        assert rec2.id == "abc123"
        assert rec2.voice_name == "my_voice"


# ──────────────────────────────────────────────
# VoiceResult
# ──────────────────────────────────────────────


class TestVoiceResult:
    def test_success_default(self):
        r = VoiceResult()
        assert r.success is True
        assert r.data == {}
        assert r.error == ""

    def test_failure(self):
        r = VoiceResult(success=False, error="boom")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error"] == "boom"

    def test_to_dict(self):
        r = VoiceResult(data={"key": "val"})
        d = r.to_dict()
        assert d["data"]["key"] == "val"
        assert d["success"] is True
        assert d["error"] == ""


# ──────────────────────────────────────────────
# VoiceStorage (JSON)
# ──────────────────────────────────────────────


class TestVoiceStorage:
    def test_empty_file_missing(self, tmp_path: Path):
        db = tmp_path / "voices.json"
        store = VoiceStorage(db)
        assert store.load() == []

    def test_add_and_get(self, tmp_path: Path):
        db = tmp_path / "voices.json"
        store = VoiceStorage(db)
        store.add({"id": "v1", "name": "hello"})
        rec = store.get("v1")
        assert rec is not None
        assert rec["name"] == "hello"

    def test_delete(self, tmp_path: Path):
        db = tmp_path / "voices.json"
        store = VoiceStorage(db)
        store.add({"id": "v1", "name": "a"})
        store.add({"id": "v2", "name": "b"})
        assert store.delete("v1") is True
        assert store.get("v1") is None
        assert store.get("v2") is not None

    def test_delete_nonexistent(self, tmp_path: Path):
        db = tmp_path / "voices.json"
        store = VoiceStorage(db)
        assert store.delete("missing") is False

    def test_corrupt_json(self, tmp_path: Path):
        db = tmp_path / "voices.json"
        db.write_text("NOT_JSON!!!", "utf-8")
        store = VoiceStorage(db)
        assert store.load() == []

    def test_chinese_preserved(self, tmp_path: Path):
        db = tmp_path / "voices.json"
        store = VoiceStorage(db)
        store.add({"id": "v1", "name": "中文音色"})
        raw = json.loads(db.read_text("utf-8"))
        assert raw[0]["name"] == "中文音色"

    def test_auto_create_parent_dir(self, tmp_path: Path):
        db = tmp_path / "sub" / "dir" / "voices.json"
        store = VoiceStorage(db)
        store.add({"id": "v1"})
        assert db.exists()


# ──────────────────────────────────────────────
# split_text
# ──────────────────────────────────────────────


class TestSplitText:
    def test_sentence_by_period(self):
        result = split_text("第一句话。第二句话。第三句话。")
        assert len(result) == 3
        assert result[0] == "第一句话。"
        assert result[2] == "第三句话。"

    def test_sentence_by_exclamation(self):
        result = split_text("你好！世界！")
        assert len(result) == 2

    def test_sentence_by_question(self):
        result = split_text("什么？为什么？")
        assert len(result) == 2

    def test_flatten_newlines(self):
        result = split_text("第一行\n第二行。")
        assert len(result) == 1
        assert "\n" not in result[0]

    def test_paragraph_mode(self):
        text = "段落一。\n\n段落二。\n\n段落三。"
        result = split_text(text, mode="paragraph")
        assert len(result) == 3

    def test_empty_string(self):
        result = split_text("")
        assert result == [""]

    def test_strips_curly_quotes(self):
        result = split_text("\u201c你好！\u201d再见！")
        assert all("\u201c" not in s and "\u201d" not in s for s in result)

    def test_strips_straight_quotes(self):
        result = split_text('"Hello!" Goodbye!')
        assert '"' not in result[0]


# ──────────────────────────────────────────────
# _guess_mime
# ──────────────────────────────────────────────


class TestGuessMime:
    def test_wav(self):
        assert _guess_mime(".wav") == "audio/wav"

    def test_mp3(self):
        assert _guess_mime(".mp3") == "audio/mpeg"

    def test_unknown(self):
        assert _guess_mime(".xyz") == "audio/wav"
