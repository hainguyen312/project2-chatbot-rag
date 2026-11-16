"""
services/memory_manager: log không chứa dữ liệu cá nhân — uid thay bằng mã băm, nội dung fact
chỉ ghi độ dài (MEMORY_LOG_CONTENT=1 để debug trên máy dev).
"""
import logging

import pytest

import services.memory_manager as M
from tests.memory_fakes import make_manager

UID = "a6MTSikt1eWmaxzprqii3aZ5cGD3"
SECRETS = ["Người dùng sinh năm 2003", "Người dùng là nam", "Người dùng đã 23 tuổi",
           "001203012345", "Bách Khoa", "Kinh tế Quốc dân", "Hạ Long"]


def D(candidate, action, target_id=None, merged=None):
    return {"candidate": candidate, "action": action, "target_id": target_id, "merged_fact": merged, "reason": "r"}


@pytest.fixture
def mm(monkeypatch):
    monkeypatch.setattr(M, "_LOG_CONTENT", False)
    return make_manager()


def _logs(caplog):
    return "\n".join(r.getMessage() for r in caplog.records if r.name == "memory_manager")


def _assert_clean(text):
    assert UID not in text
    for s in SECRETS:
        assert s not in text, s


def test_uid_hash_stable_and_opaque():
    assert M._uid(UID) == M._uid(UID) and M._uid(UID).startswith("u:") and len(M._uid(UID)) == 12
    assert UID not in M._uid(UID) and M._uid(UID) != M._uid("khac")
    assert M._uid(None) == "u:-"


def test_txt_length_only_by_default(monkeypatch):
    monkeypatch.setattr(M, "_LOG_CONTENT", False)
    assert M._txt("Người dùng sinh năm 2003") == "(24 ký tự)"
    monkeypatch.setattr(M, "_LOG_CONTENT", True)
    assert M._txt("Người dùng sinh năm 2003") == "Người dùng sinh năm 2003"


def test_add_update_noop_delete_logs_have_no_personal_data(mm, caplog):
    caplog.set_level(logging.INFO, logger="memory_manager")
    # ADD (kho trống → không gọi LLM)
    assert mm.update_memories(UID, [{"fact": "Người dùng sinh năm 2003", "type": "core"},
                                    {"fact": "Người dùng học tại Bách Khoa", "type": "core"},
                                    {"fact": "Người dùng mở quán cà phê ở Hạ Long", "type": "core"}]) == ["ADD"] * 3
    # UPDATE + NOOP + DELETE trong một lượt
    mm.client.queues["memory_decisions"].append({"decisions": [
        D(0, "UPDATE", 1, "Người dùng đã 23 tuổi, sinh năm 2003"),
        D(1, "NOOP"),
        D(2, "DELETE", 3),
    ]})
    acts = mm.update_memories(UID, [{"fact": "Người dùng đã 23 tuổi", "type": "core"},
                                    {"fact": "Người dùng là nam", "type": "core"},
                                    {"fact": "Người dùng không còn kinh doanh ở Hạ Long", "type": "core"}])
    assert sorted(acts) == ["DELETE", "NOOP", "UPDATE"]
    text = _logs(caplog)
    for action in ("ADD user=", "UPDATE user=", "NOOP user=", "DELETE user="):
        assert action in text, action
    assert M._uid(UID) in text  # vẫn lần theo được người dùng qua mã băm
    _assert_clean(text)


def test_sensitive_fact_skip_not_logged(mm, caplog):
    caplog.set_level(logging.INFO, logger="memory_manager")
    mm.client.queues["memory_facts"].append({"facts": [
        {"fact": "Số CCCD của người dùng là 001203012345", "type": "core"}]})
    assert mm.extract_memories("…", "…") == []
    text = _logs(caplog)
    assert "Bỏ qua fact nhạy cảm" in text
    _assert_clean(text)


def test_mode_and_manual_delete_logs(mm, caplog):
    caplog.set_level(logging.INFO, logger="memory_manager")
    mm.update_memories(UID, [{"fact": "Người dùng học tại Bách Khoa", "type": "core"}])
    mem_id = mm.mongo_col.docs[0]["mem_id"]
    mm.delete_memory(UID, mem_id)
    mm.delete_all_for_user(UID)
    _assert_clean(_logs(caplog))


def test_debug_flag_restores_content(monkeypatch, caplog):
    monkeypatch.setattr(M, "_LOG_CONTENT", True)
    mm = make_manager()
    caplog.set_level(logging.INFO, logger="memory_manager")
    mm.update_memories(UID, [{"fact": "Người dùng sinh năm 2003", "type": "core"}])
    assert "Người dùng sinh năm 2003" in _logs(caplog)
