"""Mục 1: trích xuất (structured outputs), cập nhật gộp, kiểm tra target_id, khoá theo user."""
import threading
import time

import pytest

import services.memory_manager as M
from tests.memory_fakes import make_manager


@pytest.fixture
def mm():
    return make_manager()


def seed(mm, user, fact, mtype="core"):
    """Thêm sẵn một memory (không qua LLM)."""
    return mm._do_add(user, {"fact": fact, "type": mtype}, mm._embed(fact), "2026-01-01T00:00:00")


def decide(*decisions):
    return {"decisions": list(decisions)}


def D(candidate, action, target_id=None, merged=None, reason="r"):
    return {"candidate": candidate, "action": action, "target_id": target_id, "merged_fact": merged, "reason": reason}


# ── Extraction ───────────────────────────────────────────────────────────────
class TestExtraction:
    def test_uses_strict_json_schema(self, mm):
        mm.client.queues["memory_facts"].append(
            {"facts": [{"fact": "Người dùng sinh năm 2003", "type": "core", "sensitive": False}]})
        assert mm.extract_memories("Tôi sinh năm 2003", "…") == [
            {"fact": "Người dùng sinh năm 2003", "type": "core", "sensitive": False}]
        rf = mm.client.chat_calls[0]["response_format"]
        assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True

    def test_filters_invalid_and_sensitive(self, mm):
        mm.client.queues["memory_facts"].append({"facts": [
            {"fact": "", "type": "core"},
            {"fact": "Số CCCD của người dùng là 001203012345", "type": "core"},
            {"fact": "Người dùng hay hỏi về thuế", "type": "semantic"},
        ]})
        assert [f["fact"] for f in mm.extract_memories("…", "…")] == ["Người dùng hay hỏi về thuế"]

    def test_profile_fact_forced_core(self, mm):
        mm.client.queues["memory_facts"].append({"facts": [{"fact": "Người dùng sinh năm 2001", "type": "episodic"}]})
        assert mm.extract_memories("…", "…")[0]["type"] == "core"

    def test_llm_error_returns_empty(self, mm):
        mm.client.queues["memory_facts"].append(RuntimeError("429"))
        assert mm.extract_memories("Tôi sinh năm 2003", "…") == []

    def test_empty_user_message_no_call(self, mm):
        assert mm.extract_memories("  ", "bot") == [] and mm.client.chat_calls == []


# ── Cập nhật gộp ─────────────────────────────────────────────────────────────
class TestUpdateMemories:
    def test_empty_store_adds_without_llm(self, mm):
        acts = mm.update_memories("u1", [{"fact": "Người dùng là nam", "type": "core"},
                                         {"fact": "Người dùng sinh năm 2003", "type": "core"}])
        assert acts == ["ADD", "ADD"] and mm.client.chat_calls == []
        assert len(mm.mongo_col.docs) == 2 and all(len(d["embedding"]) == 64 for d in mm.mongo_col.docs)

    def test_one_embedding_request_and_one_decision_call(self, mm):
        seed(mm, "u1", "Người dùng học tại Bách Khoa")
        mm.client.embed_calls.clear()
        mm.client.queues["memory_decisions"].append(decide(D(0, "ADD"), D(1, "NOOP"), D(2, "ADD")))
        mm.update_memories("u1", [{"fact": f"Fact {i} của người dùng học", "type": "semantic"} for i in range(3)])
        assert len(mm.client.embed_calls) == 1 and isinstance(mm.client.embed_calls[0], list)
        assert len(mm.client.chat_calls) == 1

    def test_llm_sees_short_ids_not_real_ids(self, mm):
        seed(mm, "u1", "Người dùng học tại Bách Khoa")
        pk = mm.mongo_col.docs[0]["mem_id"]
        mm.client.queues["memory_decisions"].append(decide(D(0, "NOOP")))
        mm.update_memories("u1", [{"fact": "Người dùng học Bách Khoa", "type": "core"}])
        prompt = mm.client.chat_calls[0]["messages"][1]["content"]
        assert '"id": 1' in prompt and str(pk) not in prompt

    def test_update_maps_short_id_and_keeps_history(self, mm):
        seed(mm, "u1", "Người dùng học tại Bách Khoa")
        original_id = mm.mongo_col.docs[0]["mem_id"]
        mm.client.queues["memory_decisions"].append(
            decide(D(0, "UPDATE", 1, "Người dùng chuyển sang học Kinh tế Quốc dân")))
        assert mm.update_memories("u1", [{"fact": "Người dùng chuyển sang học Kinh tế Quốc dân", "type": "core"}]) == ["UPDATE"]
        mem_id_before = original_id
        assert [d["fact"] for d in mm.mongo_col.active()] == ["Người dùng chuyển sang học Kinh tế Quốc dân"]
        doc = mm.mongo_col.docs[0]
        assert doc["version"] == 2 and [h["action"] for h in doc["history"]] == ["ADD", "UPDATE"]
        assert doc["mem_id"] == mem_id_before                  # sửa tại chỗ, id không đổi
        assert doc["embedding"] == mm._embed("Người dùng chuyển sang học Kinh tế Quốc dân")

    def test_delete_is_soft_in_mongo(self, mm):
        seed(mm, "u1", "Người dùng mở quán cà phê ở Hạ Long")
        mm.client.queues["memory_decisions"].append(decide(D(0, "DELETE", 1)))
        assert mm.update_memories("u1", [{"fact": "Người dùng đã đóng cửa quán", "type": "core"}]) == ["DELETE"]
        assert mm.mongo_col.active() == [] and mm.mongo_col.docs[0]["deleted"] is True

    @pytest.mark.parametrize("bad_id, action, expect", [(99, "UPDATE", "ADD"), (99, "DELETE", "NOOP"),
                                                         (None, "UPDATE", "ADD"), (None, "DELETE", "NOOP")])
    def test_invalid_target_id_never_touches_other_memory(self, mm, bad_id, action, expect):
        seed(mm, "u1", "Người dùng học tại Bách Khoa")
        mm.client.queues["memory_decisions"].append(decide(D(0, action, bad_id, "x")))
        assert mm.update_memories("u1", [{"fact": "Người dùng học ở trường khác", "type": "core"}]) == [expect]
        assert any(d["fact"] == "Người dùng học tại Bách Khoa" for d in mm.mongo_col.active())

    def test_other_users_memory_never_visible(self, mm):
        seed(mm, "u2", "Người dùng học tại Bách Khoa")
        acts = mm.update_memories("u1", [{"fact": "Người dùng học tại Bách Khoa", "type": "core"}])
        assert acts == ["ADD"] and mm.client.chat_calls == []   # không có memory của u1 → không hỏi LLM

    def test_same_target_touched_once_per_turn(self, mm):
        seed(mm, "u1", "Người dùng làm kế toán")
        mm.client.queues["memory_decisions"].append(decide(
            D(0, "UPDATE", 1, "Người dùng làm giáo viên"), D(1, "UPDATE", 1, "Người dùng làm bác sĩ")))
        acts = mm.update_memories("u1", [{"fact": "Người dùng làm giáo viên", "type": "core"},
                                         {"fact": "Người dùng làm bác sĩ", "type": "core"}])
        assert acts == ["UPDATE", "ADD"]

    def test_missing_or_garbled_decision_falls_back_to_add(self, mm):
        seed(mm, "u1", "Người dùng học tại Bách Khoa")
        mm.client.queues["memory_decisions"].append(decide(D(5, "UPDATE", 1), D(0, "XOA")))
        assert mm.update_memories("u1", [{"fact": "Người dùng học Bách Khoa ngành điện", "type": "core"},
                                         {"fact": "Người dùng ở Hà Nội", "type": "core"}]) == ["ADD", "ADD"]

    def test_decision_llm_error_falls_back_to_add(self, mm):
        seed(mm, "u1", "Người dùng học tại Bách Khoa")
        mm.client.queues["memory_decisions"].append(RuntimeError("timeout"))
        assert mm.update_memories("u1", [{"fact": "Người dùng học Bách Khoa", "type": "core"}]) == ["ADD"]

    def test_replacement_guard_turns_delete_into_update(self, mm):
        seed(mm, "u1", "Người dùng học ngành CNTT tại Bách Khoa")
        mm.client.queues["memory_decisions"].append(decide(D(0, "DELETE", 1)))
        acts = mm.update_memories("u1", [{"fact": "Người dùng chuyển sang học Đại học Kinh tế Quốc dân", "type": "core"}])
        assert acts == ["UPDATE"]

    def test_invalid_candidates_skipped(self, mm):
        assert mm.update_memories("u1", [{"fact": "", "type": "core"}, {"fact": "x", "type": "lạ"}]) == ["SKIP", "SKIP"]

    def test_not_ready(self, mm):
        mm.mongo_col = None
        assert mm.update_memories("u1", [{"fact": "a", "type": "core"}]) == ["SKIP"]

    def test_single_update_compat(self, mm):
        assert mm.update_memory("u1", {"fact": "Người dùng là nữ", "type": "core"}) == "ADD"


# ── Khoá theo user ───────────────────────────────────────────────────────────
def test_process_turn_serialized_per_user(mm, monkeypatch):
    """Hai lượt cùng user chạy song song: lượt sau phải thấy memory lượt trước ghi → không ADD trùng."""
    active, peak = [0], [0]
    orig = mm.update_memories

    def slow_update(user_id, facts):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        try:
            return orig(user_id, facts)
        finally:
            active[0] -= 1

    monkeypatch.setattr(mm, "update_memories", slow_update)
    fact = {"facts": [{"fact": "Người dùng sinh năm 2003", "type": "core"}]}
    mm.client.queues["memory_facts"] += [fact, fact]
    mm.client.queues["memory_decisions"].append(decide(D(0, "NOOP")))   # lượt 2 thấy bản lượt 1 → NOOP
    threads = [threading.Thread(target=mm.process_turn, args=("u1", "Tôi sinh năm 2003", "ok")) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert peak[0] == 1 and len(mm.mongo_col.active()) == 1


def test_different_users_not_blocked(mm):
    assert M._user_lock("a") is M._user_lock("a") and M._user_lock("a") is not M._user_lock("b")


def test_process_turn_attaches_chat_id(mm):
    mm.client.queues["memory_facts"].append({"facts": [{"fact": "Người dùng là nam", "type": "core"}]})
    out = mm.process_turn("u1", "Tôi là nam", "ok", chat_id="chat-9")
    assert out["actions"][0]["action"] == "ADD" and mm.mongo_col.docs[0]["source_chat_id"] == "chat-9"
