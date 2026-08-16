"""P10: Persona 知识库管理（B10 删除/PUT）+ B7 原子写 测试。"""

from __future__ import annotations

from clipwright.persona.repository import PersonaRepository
from clipwright.schema.persona import KnowledgeDoc
from clipwright.config import settings


def _repo(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "persona_dir", tmp_path / "personas")
    return PersonaRepository(tmp_path / "personas")


def test_knowledge_add_update_delete(tmp_path, monkeypatch) -> None:
    repo = _repo(tmp_path, monkeypatch)
    pid = "persona_btest"
    repo.save_manifest(_manifest(pid))

    doc = KnowledgeDoc(title="主题", content="知识内容", source="test")
    doc_id = repo.add_knowledge_doc(pid, doc)
    assert repo.load_manifest(pid).knowledge[0].id == doc_id

    # PUT 更新
    updated = KnowledgeDoc(id=doc_id, title="新主题", content="新内容", source="test")
    assert repo.update_knowledge_doc(pid, doc_id, updated) is True
    manifest = repo.load_manifest(pid)
    assert manifest.knowledge[0].title == "新主题"
    assert manifest.knowledge[0].content == "新内容"

    # DELETE
    assert repo.delete_knowledge_doc(pid, doc_id) is True
    remaining = repo.load_manifest(pid).knowledge or []
    assert remaining == []

    # 重复删除 → False
    assert repo.delete_knowledge_doc(pid, doc_id) is False


def test_atomic_write_no_tmp_left(tmp_path, monkeypatch) -> None:
    """B7: 原子写后无 .tmp 残留。"""
    repo = _repo(tmp_path, monkeypatch)
    pid = "persona_atomic"
    repo.save_manifest(_manifest(pid))
    pdir = repo.persona_path(pid)
    tmps = list(pdir.glob("*.tmp"))
    assert tmps == []
    # 文件内容有效
    assert (pdir / "persona.yaml").exists()
    assert (pdir / "parameter.yaml").exists()


def _manifest(persona_id: str):
    from clipwright.schema.persona import PersonaManifest
    return PersonaManifest(
        persona_id=persona_id,
        persona_name="测试人格",
        parameter={"persona_id": persona_id, "identity": {"tone": "neutral"}},
    )
