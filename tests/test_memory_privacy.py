"""Mục 4: dữ liệu nhạy cảm, chế độ bộ nhớ theo người dùng, kiểm tra quyền truy cập memory."""
import pytest

import services.memory_manager as M
from services import auth_guard as G
from tests.memory_fakes import make_manager


@pytest.fixture
def mm():
    return make_manager()


def facts(*items):
    return {"facts": [{"fact": f, "type": t, "sensitive": s} for f, t, s in items]}


TURN = facts(("Người dùng làm kế toán ở Đà Nẵng", "core", False),
             ("Người dùng đang bị khởi tố vì tội trộm cắp", "episodic", True))


class TestSensitiveDetection:
    @pytest.mark.parametrize("fact", [
        "Người dùng đang bị khởi tố", "Người dùng có tiền án", "Người dùng bị trầm cảm",
        "Người dùng đang mang thai tháng thứ 5", "Người dùng theo đạo Công giáo", "Người dùng nhiễm HIV",
    ])
    def test_regex_safety_net(self, fact):
        assert M.is_sensitive_topic(fact)

    @pytest.mark.parametrize("fact", ["Người dùng làm kế toán", "Người dùng sinh năm 2003", "Thích trả lời ngắn"])
    def test_ordinary_facts(self, fact):
        assert not M.is_sensitive_topic(fact)

    def test_regex_flags_even_if_llm_says_false(self, mm):
        mm.client.queues["memory_facts"].append(facts(("Người dùng có tiền án trộm cắp", "core", False)))
        assert mm.extract_memories("…", "…")[0]["sensitive"] is True

    def test_schema_requires_sensitive_flag(self):
        assert "sensitive" in M._EXTRACT_SCHEMA["schema"]["properties"]["facts"]["items"]["required"]


class TestModes:
    def test_default_mode_is_privacy_first(self, mm):
        assert mm.get_mode("u1") == "no_sensitive"

    def test_default_drops_sensitive_facts(self, mm):
        mm.client.queues["memory_facts"].append(TURN)
        out = mm.process_turn("u1", "…", "…")
        assert out["dropped_sensitive"] == 1
        assert [d["fact"] for d in mm.mongo_col.docs] == ["Người dùng làm kế toán ở Đà Nẵng"]

    def test_full_mode_keeps_and_marks_sensitive(self, mm):
        mm.set_mode("u1", "full")
        mm.client.queues["memory_facts"].append(TURN)
        mm.client.queues["memory_decisions"].append(
            {"decisions": [{"candidate": 1, "action": "ADD", "target_id": None, "merged_fact": None, "reason": ""},
                           {"candidate": 0, "action": "ADD", "target_id": None, "merged_fact": None, "reason": ""}]})
        mm.process_turn("u1", "…", "…")
        assert {d["fact"]: d["sensitive"] for d in mm.mongo_col.docs} == {
            "Người dùng làm kế toán ở Đà Nẵng": False, "Người dùng đang bị khởi tố vì tội trộm cắp": True}

    def test_off_mode_neither_writes_nor_reads(self, mm):
        mm._do_add("u1", {"fact": "Người dùng sinh năm 2003", "type": "core"}, mm._embed("x"), M._now_iso())
        mm.set_mode("u1", "off")
        assert mm.process_turn("u1", "Tôi là nam", "ok") == {"skipped": True, "reason": "memory_off"}
        assert mm.client.chat_calls == []
        assert mm.retrieve_memories("u1", "năm sinh") == []

    def test_no_sensitive_hides_previously_stored_sensitive(self, mm):
        for f in ("Người dùng sinh năm 2003", "Người dùng bị trầm cảm"):
            mm._do_add("u1", {"fact": f, "type": "core"}, mm._embed(f), M._now_iso())
        assert [m["fact"] for m in mm.retrieve_memories("u1", "người dùng")] == ["Người dùng sinh năm 2003"]
        mm.set_mode("u1", "full")
        assert len(mm.retrieve_memories("u1", "người dùng")) == 2

    def test_panel_marks_sensitive_even_for_old_records(self, mm):
        mm._do_add("u1", {"fact": "Người dùng có tiền án", "type": "core"}, mm._embed("x"), M._now_iso())
        mm.mongo_col.docs[0].pop("sensitive")        # bản ghi cũ trước khi có trường này
        assert mm.list_user_memories("u1")[0]["sensitive"] is True

    def test_invalid_mode_rejected(self, mm):
        with pytest.raises(ValueError):
            mm.set_mode("u1", "everything")

    def test_modes_are_per_user(self, mm):
        mm.set_mode("u1", "off")
        assert mm.get_mode("u2") == "no_sensitive"


# ── Quyền truy cập memory ────────────────────────────────────────────────────
def verify(token):
    if token == "good-u1":
        return {"uid": "u1firebase"}
    raise ValueError("bad token")


class TestAuthGuard:
    def test_valid_token_wins_over_body(self):
        assert G.resolve_user_id("u2firebase", "Bearer good-u1", verify) == "u1firebase"

    def test_anonymous_id_without_token_ok(self):
        assert G.resolve_user_id("demo_ab12cd34", None, verify) == "demo_ab12cd34"

    @pytest.mark.parametrize("auth", [None, "Bearer bad", "Basic xyz"])
    def test_firebase_uid_without_valid_token_rejected(self, auth):
        # Lỗ hổng cũ: body user_id được tin → đọc memory người khác qua /rag/chat
        assert G.resolve_user_id("u1firebase", auth, verify) is None

    def test_enforce_own_memory(self):
        assert G.enforce_user_match("u1firebase", "Bearer good-u1", verify) == "u1firebase"

    def test_enforce_other_users_memory_forbidden(self):
        with pytest.raises(G.AuthError) as e:
            G.enforce_user_match("u2firebase", "Bearer good-u1", verify)
        assert e.value.status == 403

    @pytest.mark.parametrize("auth", [None, "Bearer bad"])
    def test_enforce_firebase_uid_needs_token(self, auth):
        with pytest.raises(G.AuthError) as e:
            G.enforce_user_match("u1firebase", auth, verify)
        assert e.value.status == 401

    def test_enforce_anonymous_ok(self):
        assert G.enforce_user_match("demo_ab12cd34", None, verify) == "demo_ab12cd34"
