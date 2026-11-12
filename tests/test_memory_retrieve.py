"""Mục 2: độ mới, hết hạn episodic, ngưỡng tương đồng khi truy xuất memory."""
from datetime import datetime, timedelta

import pytest

import services.memory_manager as M
from tests.memory_fakes import make_manager

NOW = datetime(2026, 11, 12, 12, 0, 0)


def ago(days: float) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat()


@pytest.fixture
def mm():
    return make_manager()


def put(mm, user, fact, mtype, days_ago):
    return mm._do_add(user, {"fact": fact, "type": mtype}, mm._embed(fact), ago(days_ago))


class TestRecency:
    @pytest.mark.parametrize("mtype", ["core", "procedural"])
    def test_profile_and_preferences_never_fade(self, mtype):
        assert M.recency_factor(mtype, (NOW - timedelta(days=3000)).isoformat(), NOW) == 1.0

    def test_episodic_half_life(self):
        half = M.MEMORY_HALF_LIFE_DAYS["episodic"]
        assert M.recency_factor("episodic", (NOW - timedelta(days=half)).isoformat(), NOW) == pytest.approx(0.5)
        assert M.recency_factor("episodic", NOW.isoformat(), NOW) == pytest.approx(1.0)

    def test_semantic_fades_slower_than_episodic(self):
        ts = (NOW - timedelta(days=60)).isoformat()
        assert M.recency_factor("semantic", ts, NOW) > M.recency_factor("episodic", ts, NOW)

    def test_bad_timestamp_treated_as_fresh(self):
        assert M.recency_factor("episodic", "không phải ngày", NOW) == 1.0
        assert M.recency_factor("episodic", None, NOW) == 1.0

    def test_timezone_aware_timestamp(self):
        assert 0 < M.recency_factor("episodic", "2026-10-13T12:00:00+07:00", NOW) < 1

    def test_score_bounds(self):
        old = (NOW - timedelta(days=10_000)).isoformat()
        assert M.retrieval_score(0.8, "episodic", old, NOW) == pytest.approx(0.8 * (1 - M.MEMORY_RECENCY_WEIGHT), abs=1e-6)
        assert M.retrieval_score(0.8, "core", old, NOW) == pytest.approx(0.8)


class TestExpiry:
    def test_episodic_expires_after_ttl(self):
        ttl = M.MEMORY_EPISODIC_TTL_DAYS
        assert M.is_expired("episodic", (NOW - timedelta(days=ttl + 1)).isoformat(), NOW)
        assert not M.is_expired("episodic", (NOW - timedelta(days=ttl - 1)).isoformat(), NOW)

    @pytest.mark.parametrize("mtype", ["core", "semantic", "procedural"])
    def test_other_types_never_expire(self, mtype):
        assert not M.is_expired(mtype, (NOW - timedelta(days=5000)).isoformat(), NOW)


class TestRetrieve:
    def test_expired_episodic_excluded_but_still_listed(self, mm):
        put(mm, "u1", "Người dùng đang tranh chấp hợp đồng thuê nhà với chủ trọ", "episodic", 400)
        got = mm.retrieve_memories("u1", "tranh chấp hợp đồng thuê nhà chủ trọ")
        assert got == []
        listed = mm.list_user_memories("u1")
        assert len(listed) == 1 and listed[0]["expired"] is True

    def test_newer_episodic_ranks_above_older_similar_one(self, mm):
        put(mm, "u1", "Người dùng tranh chấp hợp đồng thuê nhà", "episodic", 150)
        put(mm, "u1", "Người dùng tranh chấp hợp đồng lao động", "episodic", 2)
        got = mm.retrieve_memories("u1", "tranh chấp hợp đồng", top_k=2)
        assert [m["fact"] for m in got][0] == "Người dùng tranh chấp hợp đồng lao động"

    def test_below_similarity_threshold_dropped(self, mm, monkeypatch):
        monkeypatch.setattr(M, "MEMORY_MIN_SIMILARITY", 0.5)
        put(mm, "u1", "Người dùng thích câu trả lời ngắn", "procedural", 1)
        assert mm.retrieve_memories("u1", "thủ tục đăng ký kinh doanh hộ cá thể") == []

    def test_core_always_included_even_if_unrelated(self, mm, monkeypatch):
        monkeypatch.setattr(M, "MEMORY_MIN_SIMILARITY", 0.99)
        put(mm, "u1", "Người dùng sinh năm 2003", "core", 900)
        got = mm.retrieve_memories("u1", "đủ tuổi kết hôn chưa")
        assert [m["fact"] for m in got] == ["Người dùng sinh năm 2003"]

    def test_top_k_respected_after_rerank(self, mm):
        for i in range(8):
            put(mm, "u1", f"Người dùng quan tâm luật lao động mục {i}", "semantic", i)
        got = [m for m in mm.retrieve_memories("u1", "luật lao động", top_k=3) if m["type"] == "semantic"]
        assert len(got) == 3

    def test_isolated_by_user(self, mm):
        put(mm, "u2", "Người dùng sinh năm 1990", "core", 1)
        assert mm.retrieve_memories("u1", "năm sinh") == []

    def test_store_failure_degrades_gracefully(self, mm):
        put(mm, "u1", "Người dùng sinh năm 2003", "core", 1)
        mm.mongo_col.fail_find = True
        assert mm.retrieve_memories("u1", "tuổi") == []        # không làm chết request chính

    def test_embedding_failure_still_returns_profile(self, mm):
        put(mm, "u1", "Người dùng sinh năm 2003", "core", 1)
        mm.client.embeddings.create = lambda **k: (_ for _ in ()).throw(RuntimeError("429"))
        assert [m["fact"] for m in mm.retrieve_memories("u1", "tuổi")] == ["Người dùng sinh năm 2003"]

    def test_records_without_embedding_skipped_in_search(self, mm):
        put(mm, "u1", "Người dùng tranh chấp hợp đồng thuê nhà", "episodic", 1)
        mm.mongo_col.docs[0].pop("embedding")                  # bản ghi cũ chưa migrate
        assert mm.retrieve_memories("u1", "tranh chấp hợp đồng thuê nhà") == []

    def test_panel_list_never_returns_embeddings(self, mm):
        put(mm, "u1", "Người dùng sinh năm 2003", "core", 1)
        assert "embedding" not in mm.list_user_memories("u1")[0]

    def test_delete_one_and_all(self, mm):
        put(mm, "u1", "A người dùng", "core", 1)
        put(mm, "u1", "B người dùng", "core", 1)
        put(mm, "u2", "C người dùng", "core", 1)
        assert mm.delete_memory("u1", mm.mongo_col.docs[0]["mem_id"]) is True
        assert mm.delete_memory("u2", mm.mongo_col.docs[0]["mem_id"]) is False   # không xoá được của người khác
        assert mm.delete_all_for_user("u1") == 1 and [d["user_id"] for d in mm.mongo_col.docs] == ["u2"]

    def test_format_for_context_groups_by_type(self, mm):
        text = mm.format_for_context([{"fact": "A", "type": "episodic"}, {"fact": "B", "type": "core"}])
        assert text.index("(core) B") < text.index("(episodic) A")
