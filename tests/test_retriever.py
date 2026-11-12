"""GraphRAGRetriever: hybrid RRF, rerank, vector search, graph fallback (retrieve/build_graph.py)."""
from types import SimpleNamespace

import pytest

import retrieve.build_graph as BG
import retrieve.lexical_bm25 as LB
from retrieve.build_graph import GraphRAGRetriever
from tests.conftest import FakeNeo4jDriver


def make_retriever(vec=None, ctx=None, bm25=None, monkeypatch=None, ctx_available=True):
    """Retriever với 3 nguồn kịch bản; ghi lại số lần embed."""
    r = GraphRAGRetriever(FakeNeo4jDriver(), milvus_collection=None, openai_client=None)
    r.embed_calls = 0

    def _embed(q):
        r.embed_calls += 1
        return [0.1, 0.2]

    r._embed = _embed
    r._vector_search_scored = lambda q, top_k, vec=None: list(vec_list)[:top_k]
    r._ctx_search_scored = lambda q, top_k, vec=None: list(ctx_list)[:top_k]
    r._ctx_collection, r._ctx_checked = (object() if ctx_available else None), True
    vec_list, ctx_list = vec or [], ctx or []
    if monkeypatch is not None:
        idx = SimpleNamespace(search=lambda q, top_k=50: list(bm25 or [])[:top_k])
        monkeypatch.setattr(LB, "get_bm25", lambda: idx)
    return r


class TestHybridSearch:
    def test_rrf_rewards_agreement(self, monkeypatch):
        # B đứng hạng 2 ở cả hai nguồn → vượt A (hạng 1 chỉ ở vector) và C (hạng 1 chỉ ở BM25)
        r = make_retriever(vec=[("A", .9), ("B", .8)], bm25=[("C", 9.0), ("B", 7.0)], monkeypatch=monkeypatch)
        out = r._hybrid_search("q", top_k=3, mode="hybrid")
        assert [d["mapc"] for d in out][0] == "B"

    def test_scores_normalized_to_unit_interval(self, monkeypatch):
        r = make_retriever(vec=[("A", .9)], bm25=[("A", 5.0)], monkeypatch=monkeypatch)
        out = r._hybrid_search("q", top_k=5, mode="hybrid")
        assert out[0]["score"] == pytest.approx(1.0)  # hạng 1 ở mọi nguồn = điểm tối đa
        assert all(0 < d["score"] <= 1.0 + 1e-9 for d in out)

    def test_keeps_per_source_rank_and_score(self, monkeypatch):
        r = make_retriever(vec=[("A", .9), ("B", .5)], bm25=[("B", 3.0)], monkeypatch=monkeypatch)
        b = next(d for d in r._hybrid_search("q", 5, mode="hybrid") if d["mapc"] == "B")
        assert (b["vec_rank"], b["vec_score"], b["bm25_rank"], b["bm25_score"]) == (2, .5, 1, 3.0)

    @pytest.mark.parametrize("mode, expect", [
        ("vector", {"A"}),
        ("bm25", {"C"}),
        ("ctx", {"X"}),
        ("hybrid", {"A", "C"}),
        ("hybrid3", {"A", "C", "X"}),
    ])
    def test_modes_select_sources(self, monkeypatch, mode, expect):
        r = make_retriever(vec=[("A", .9)], ctx=[("X", .7)], bm25=[("C", 4.0)], monkeypatch=monkeypatch)
        assert {d["mapc"] for d in r._hybrid_search("q", 10, mode=mode)} == expect

    def test_query_embedded_once_for_two_vector_sources(self, monkeypatch):
        r = make_retriever(vec=[("A", .9)], ctx=[("X", .7)], monkeypatch=monkeypatch)
        r._hybrid_search("q", 5, mode="hybrid3")
        assert r.embed_calls == 1

    def test_bm25_mode_does_not_embed(self, monkeypatch):
        r = make_retriever(bm25=[("C", 4.0)], monkeypatch=monkeypatch)
        r._hybrid_search("q", 5, mode="bm25")
        assert r.embed_calls == 0

    def test_weight_override(self, monkeypatch):
        r = make_retriever(vec=[("A", .9)], bm25=[("C", 4.0)], monkeypatch=monkeypatch)
        out = r._hybrid_search("q", 5, mode="hybrid", weights={"bm25": 2.0})
        assert out[0]["mapc"] == "C"

    def test_default_mode_is_hybrid_even_with_ctx(self, monkeypatch):
        monkeypatch.delenv("RETRIEVAL_MODE", raising=False)
        r = make_retriever(vec=[("A", .9)], ctx=[("X", .7)], bm25=[("C", 4.0)], monkeypatch=monkeypatch)
        assert {d["mapc"] for d in r._hybrid_search("q", 10)} == {"A", "C"}

    def test_env_mode(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_MODE", "vector")
        r = make_retriever(vec=[("A", .9)], bm25=[("C", 4.0)], monkeypatch=monkeypatch)
        assert {d["mapc"] for d in r._hybrid_search("q", 10)} == {"A"}

    def test_no_results(self, monkeypatch):
        r = make_retriever(monkeypatch=monkeypatch)
        assert r._hybrid_search("q", 10, mode="hybrid") == []

    def test_bm25_unavailable_falls_back_to_vector(self, monkeypatch):
        r = make_retriever(vec=[("A", .9)], monkeypatch=monkeypatch)
        monkeypatch.setattr(LB, "get_bm25", lambda: None)
        assert [d["mapc"] for d in r._hybrid_search("q", 10, mode="hybrid")] == ["A"]

    def test_top_k_respected(self, monkeypatch):
        r = make_retriever(vec=[(f"V{i}", 1 - i / 100) for i in range(30)], monkeypatch=monkeypatch)
        assert len(r._hybrid_search("q", 7, mode="vector")) == 7


class TestRerank:
    @pytest.fixture
    def r(self):
        return GraphRAGRetriever(FakeNeo4jDriver(), None, None)

    def cand(self, mapc, ten, noidung, base):
        return {"mapc": mapc, "ten": ten, "noidung": noidung, "base_score": base}

    def test_scores_bounded(self, r):
        out = r._rerank([self.cand("A", "Điều 1.1.LQ.1. Kết hôn", "kết hôn " * 50, 5.0),
                         self.cand("B", "x", "y", -3.0)], "kết hôn", top_k=5)
        assert all(0.0 <= c["score"] <= 1.0 for c in out)

    def test_stopwords_do_not_count(self, r):
        # Bản cũ: mọi token trùng (kể cả "của", "và") đều +0.05 → B thắng nhờ hư từ
        a = self.cand("A", "Điều 1.1.LQ.1. Bảo vệ thai sản", "lao động nữ mang thai", 0.5)
        b = self.cand("B", "Điều 2.2.LQ.2. Khác", "của và là có không được", 0.5)
        out = r._rerank([b, a], "lao động nữ mang thai có được của và", top_k=2)
        assert out[0]["mapc"] == "A"

    def test_title_match_weighs_double(self, r):
        a = self.cand("A", "Điều 1.1.LQ.1. Án treo", "nội dung khác", 0.5)
        b = self.cand("B", "Điều 2.2.LQ.2. Khác", "án treo", 0.5)
        out = r._rerank([b, a], "án treo", top_k=2)
        assert out[0]["mapc"] == "A" and out[0]["lex_coverage"] > out[1]["lex_coverage"]

    def test_base_score_dominates_by_default(self, r):
        # 0.7·base: điều retrieval chắc chắn hơn nhiều không bị một chút trùng từ lật đổ
        a = self.cand("A", "Điều 1.1.LQ.1. X", "không liên quan", 1.0)
        b = self.cand("B", "Điều 2.2.LQ.2. Y", "kết hôn", 0.2)
        assert r._rerank([b, a], "kết hôn", top_k=2)[0]["mapc"] == "A"

    def test_dedup_by_mapc(self, r):
        out = r._rerank([self.cand("A", "t", "n", .5), self.cand("A", "t", "n", .9)], "n", top_k=5)
        assert len(out) == 1

    def test_top_k(self, r):
        out = r._rerank([self.cand(str(i), "t", "n", i / 10) for i in range(10)], "n", top_k=3)
        assert [c["mapc"] for c in out] == ["9", "8", "7"]


class TestVectorSearchScored:
    def hit(self, meta, dist):
        return SimpleNamespace(entity=SimpleNamespace(get=lambda k: meta), distance=dist)

    def test_parses_metadata_and_dedups(self):
        col = SimpleNamespace(search=lambda **k: [[
            self.hit({"mapc": "A"}, .9), self.hit('{"mapc": "B"}', .8), self.hit({"mapc": "A"}, .7),
            self.hit({}, .6),
        ]])
        r = GraphRAGRetriever(FakeNeo4jDriver(), col, None)
        assert r._vector_search_scored("q", 10, vec=[0.1]) == [("A", .9), ("B", .8)]

    def test_no_collection(self):
        assert GraphRAGRetriever(FakeNeo4jDriver(), None, None)._vector_search_scored("q", 5) == []

    def test_search_error_returns_empty(self):
        def boom(**k):
            raise RuntimeError("milvus down")
        r = GraphRAGRetriever(FakeNeo4jDriver(), SimpleNamespace(search=boom), None)
        assert r._vector_search_scored("q", 5, vec=[0.1]) == []

    def test_legacy_api_returns_mapcs_only(self):
        r = GraphRAGRetriever(FakeNeo4jDriver(), None, None)
        r._vector_search_scored = lambda q, top_k, vec=None: [("A", .9), ("B", .8)]
        assert r._vector_search("q", 2) == ["A", "B"]


class TestGraphExpand:
    def test_falls_back_to_mysql_when_neo4j_empty(self, monkeypatch):
        r = GraphRAGRetriever(FakeNeo4jDriver(), None, None)
        monkeypatch.setattr(r, "_graph_expand_neo4j", lambda seeds, hop: [])
        monkeypatch.setattr(r, "_graph_expand_mysql", lambda seeds, hop: [{"mapc": "S"}, {"mapc": "N1"}])
        assert [x["mapc"] for x in r._graph_expand(["S"], hop=1)] == ["S", "N1"]

    def test_keeps_neo4j_when_it_has_neighbors(self, monkeypatch):
        r = GraphRAGRetriever(FakeNeo4jDriver(), None, None)
        monkeypatch.setattr(r, "_graph_expand_neo4j", lambda s, h: [{"mapc": "S"}, {"mapc": "N1"}, {"mapc": "N2"}])
        monkeypatch.setattr(r, "_graph_expand_mysql", lambda s, h: pytest.fail("không được gọi MySQL"))
        assert len(r._graph_expand(["S"], hop=1)) == 3

    def test_neo4j_error_falls_back(self, monkeypatch):
        r = GraphRAGRetriever(FakeNeo4jDriver(), None, None)

        def boom(s, h):
            raise RuntimeError("neo4j down")
        monkeypatch.setattr(r, "_graph_expand_neo4j", boom)
        monkeypatch.setattr(r, "_graph_expand_mysql", lambda s, h: [{"mapc": "S"}, {"mapc": "N"}])
        assert len(r._graph_expand(["S"], hop=1)) == 2

    def test_empty_seeds(self):
        assert GraphRAGRetriever(FakeNeo4jDriver(), None, None)._graph_expand([], hop=1) == []
