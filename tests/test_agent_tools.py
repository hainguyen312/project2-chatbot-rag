"""Các tool của agent ReAct (services/agentic_rag.execute_agent_tool) + dữ liệu gửi cho LLM."""
import json

import pytest

import services.agentic_rag as A
from services.agentic_rag import execute_agent_tool, hit_to_llm_item, split_phapdien_title
from tests.conftest import FakeNeo4jDriver


def run(name, args, retriever, rerank_query=""):
    content, hits = execute_agent_tool(name, args, retriever=retriever,
                                       neo4j_driver=FakeNeo4jDriver(), rerank_query=rerank_query)
    return json.loads(content), hits


# ── Dữ liệu gửi LLM ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("ten, ma, rest", [
    ("Điều 39.13.NĐ.75.8. Xử phạt người điều khiển xe đạp", "39.13.NĐ.75.8", "Xử phạt người điều khiển xe đạp"),
    ("Điều 8.4.LQ.8. Điều kiện kết hôn", "8.4.LQ.8", "Điều kiện kết hôn"),
    ("Tổng hợp từ web", "", "Tổng hợp từ web"),
    ("", "", ""),
])
def test_split_phapdien_title(ten, ma, rest):
    assert split_phapdien_title(ten) == (ma, rest)


class TestLlmItem:
    def test_fields(self, articles):
        item = hit_to_llm_item({**articles["M_XEDAP"], "score": 0.734})
        assert item["ma_phap_dien"] == "39.13.NĐ.75.8"
        assert item["ten"].startswith("Xử phạt")            # tên KHÔNG còn mã (tránh đọc "NĐ.75" = Nghị định 75)
        assert "100/2019/NĐ-CP" in item["vbqppl"]            # văn bản gốc để trích dẫn
        assert item["diem_lien_quan"] == 0.73
        assert "passage" not in item                         # không gửi trùng nội dung

    def test_truncates_long_content(self, articles, monkeypatch):
        monkeypatch.setattr(A, "LLM_MAX_NOIDUNG_CHARS", 50)
        item = hit_to_llm_item({**articles["M_KETHON"], "noidung": "x" * 500, "score": 1})
        assert len(item["noidung"]) <= 50 and item["noidung_truncated"] is True


# ── vector_search ────────────────────────────────────────────────────────────
class TestVectorSearch:
    def test_returns_hits_with_real_scores(self, fake_retriever, fake_mysql):
        fake_retriever.fused = [{"mapc": "M_KETHON", "score": 0.95}, {"mapc": "M_THAISAN", "score": 0.40}]
        items, hits = run("vector_search", {"query": "điều kiện kết hôn", "top_k": 5}, fake_retriever)
        assert [h["mapc"] for h in hits][0] == "M_KETHON"
        assert len({h["score"] for h in hits}) == 2          # không còn gán 0.8 cho mọi hit
        assert all(h["source"] == "phapdien" for h in hits)
        assert items[0]["ma_phap_dien"] == "8.4.LQ.8" and items[0]["vbqppl"]

    def test_top_k_respected(self, fake_retriever, fake_mysql):
        fake_retriever.fused = [{"mapc": m, "score": 0.5} for m in ("M_KETHON", "M_THAISAN", "M_XEDAP")]
        _, hits = run("vector_search", {"query": "lao động", "top_k": 2}, fake_retriever)
        assert len(hits) == 2

    def test_pool_larger_than_top_k(self, fake_retriever, fake_mysql):
        fake_retriever.fused = [{"mapc": "M_KETHON", "score": 0.9}]
        run("vector_search", {"query": "q", "top_k": 5}, fake_retriever)
        assert fake_retriever.calls[0][2] == min(5 * A.AGENT_VECTOR_POOL_MULT, 40)

    def test_rerank_uses_agent_query_and_user_query(self, fake_retriever, fake_mysql, monkeypatch):
        seen = {}
        orig = A.rerank_phapdien_hits

        def spy(retriever, q, hits, *, top_k):
            seen["q"] = q
            return orig(retriever, q, hits, top_k=top_k)

        monkeypatch.setattr(A, "rerank_phapdien_hits", spy)
        fake_retriever.fused = [{"mapc": "M_KETHON", "score": 0.9}]
        run("vector_search", {"query": "tuổi kết hôn"}, fake_retriever, rerank_query="điều kiện kết hôn")
        assert seen["q"] == "tuổi kết hôn điều kiện kết hôn"

    @pytest.mark.parametrize("args", [{"query": ""}, {"query": "   "}, {}])
    def test_empty_query(self, fake_retriever, fake_mysql, args):
        items, hits = run("vector_search", args, fake_retriever)
        assert items == [] and hits == [] and fake_retriever.calls == []

    def test_no_results(self, fake_retriever, fake_mysql):
        fake_retriever.fused = []
        assert run("vector_search", {"query": "q"}, fake_retriever) == ([], [])

    def test_mapc_missing_in_db_is_skipped(self, fake_retriever, fake_mysql):
        fake_retriever.fused = [{"mapc": "KHONG_TON_TAI", "score": 0.9}, {"mapc": "M_KETHON", "score": 0.5}]
        _, hits = run("vector_search", {"query": "kết hôn"}, fake_retriever)
        assert [h["mapc"] for h in hits] == ["M_KETHON"]


# ── graph_traverse ───────────────────────────────────────────────────────────
class TestGraphTraverse:
    def graph_row(self, art, base):
        return {**art, "base_score": base}

    def test_expands_and_reranks(self, fake_retriever, articles):
        fake_retriever.graph_rows = [self.graph_row(articles["M_XEDAP"], 0.6),
                                     self.graph_row(articles["M_THAISAN"], 1.0)]
        items, hits = run("graph_traverse", {"mapcs": ["M_THAISAN"]}, fake_retriever, rerank_query="mang thai")
        assert hits[0]["mapc"] == "M_THAISAN" and len(items) == 2

    @pytest.mark.parametrize("hop_in, hop_used", [(None, 1), (1, 1), (2, 2), (5, 2), (0, 1), (-3, 1)])
    def test_hop_clamped(self, fake_retriever, hop_in, hop_used):
        args = {"mapcs": ["M_KETHON"]} | ({} if hop_in is None else {"hop": hop_in})
        run("graph_traverse", args, fake_retriever)
        assert fake_retriever.calls[0] == ("graph", ("M_KETHON",), hop_used)

    def test_mapcs_not_a_list(self, fake_retriever):
        run("graph_traverse", {"mapcs": "M_KETHON"}, fake_retriever)
        assert fake_retriever.calls[0][1] == ()

    def test_no_neighbors(self, fake_retriever):
        assert run("graph_traverse", {"mapcs": ["M_KETHON"]}, fake_retriever) == ([], [])


# ── web_search ───────────────────────────────────────────────────────────────
class TestWebSearch:
    @pytest.fixture
    def tavily(self, monkeypatch):
        import retrieve.tavily_fallback as T
        calls = []

        def search(query, top_k=5):
            calls.append(query)
            return [{"ten": "Mức phạt mới", "noidung": "N" * 5000, "url": "https://thuvienphapluat.vn/x",
                     "trust_level": "high", "source_label": "[Web]"}]

        monkeypatch.setattr(T, "search_tavily", search)
        return calls

    def test_result_shape(self, fake_retriever, tavily):
        items, hits = run("web_search", {"query": "mức phạt mũ bảo hiểm"}, fake_retriever)
        assert tavily == ["mức phạt mũ bảo hiểm"]
        assert hits[0]["source"] == "web" and hits[0]["url"].startswith("https://")
        assert items[0]["do_tin_cay_nguon"] == "high"
        assert set(items[0]) == {"ten", "noidung", "url", "do_tin_cay_nguon"}  # không gửi trùng content/noidung

    def test_content_truncated_for_llm_not_for_ui(self, fake_retriever, tavily):
        items, hits = run("web_search", {"query": "q"}, fake_retriever)
        assert len(items[0]["noidung"]) <= A.LLM_MAX_WEB_CHARS
        assert len(hits[0]["noidung"]) == 5000

    def test_empty_query_does_not_search(self, fake_retriever, tavily):
        assert run("web_search", {"query": " "}, fake_retriever) == ([], []) and tavily == []


# ── get_full_article ─────────────────────────────────────────────────────────
class TestGetFullArticle:
    def test_found(self, fake_retriever, fake_mysql):
        payload, hits = run("get_full_article", {"mapc": "M_XEDAP"}, fake_retriever)
        assert payload["ma_phap_dien"] == "39.13.NĐ.75.8"
        assert payload["ten"].startswith("Xử phạt") and "100/2019" in payload["vbqppl"]
        assert hits[0]["score"] == 1.0

    def test_not_found(self, fake_retriever, fake_mysql):
        payload, hits = run("get_full_article", {"mapc": "KHONG_CO"}, fake_retriever)
        assert "error" in payload and hits == []

    def test_missing_mapc(self, fake_retriever, fake_mysql):
        payload, _ = run("get_full_article", {}, fake_retriever)
        assert payload == {"error": "Thiếu mapc"}

    def test_full_content_not_truncated(self, fake_retriever, fake_mysql, articles, monkeypatch):
        monkeypatch.setattr(A, "LLM_MAX_NOIDUNG_CHARS", 10)
        payload, _ = run("get_full_article", {"mapc": "M_KETHON"}, fake_retriever)
        assert payload["noidung"] == articles["M_KETHON"]["noidung"]


# ── Lỗi ──────────────────────────────────────────────────────────────────────
def test_unknown_tool(fake_retriever):
    payload, hits = run("delete_database", {}, fake_retriever)
    assert payload == {"error": "Unknown tool: delete_database"} and hits == []


def test_tool_exception_returns_error_not_raise(fake_retriever, fake_mysql):
    def boom(*a, **k):
        raise RuntimeError("milvus down")
    fake_retriever._hybrid_search = boom
    payload, hits = run("vector_search", {"query": "q"}, fake_retriever)
    assert payload == {"error": "milvus down"} and hits == []


def test_bad_top_k_type_returns_error(fake_retriever, fake_mysql):
    payload, _ = run("vector_search", {"query": "q", "top_k": "mười"}, fake_retriever)
    assert "error" in payload


def test_tool_schemas_match_executor():
    """Mọi tool khai báo cho LLM đều được execute_agent_tool xử lý (không rơi vào Unknown tool)."""
    names = {t["function"]["name"] for t in A.RAG_AGENT_TOOLS}
    assert names == {"vector_search", "graph_traverse", "web_search", "get_full_article"}
    for t in A.RAG_AGENT_TOOLS:
        params = t["function"]["parameters"]
        assert params["type"] == "object" and set(params["required"]) <= set(params["properties"])


# ── LLM truyền mã Pháp Điển thay cho mapc ────────────────────────────────────
class TestResolveArticleIds:
    @pytest.fixture(autouse=True)
    def lookup(self, monkeypatch):
        table = {"20.2.LQ.137": "M_THAISAN", "8.4.LQ.8": "M_KETHON"}
        monkeypatch.setattr(A, "_lookup_mapc_by_code", lambda codes: {c: table[c] for c in codes if c in table})

    def test_code_resolved_mapc_untouched(self):
        assert A.resolve_article_ids(["20.2.LQ.137", "M_XEDAP"]) == ["M_THAISAN", "M_XEDAP"]

    @pytest.mark.parametrize("raw", ["Điều 8.4.LQ.8", "8.4.LQ.8.", " 8.4.LQ.8 "])
    def test_tolerates_prefix_and_trailing_dot(self, raw):
        assert A.resolve_article_ids([raw]) == ["M_KETHON"]

    def test_unknown_code_kept_so_tool_reports_not_found(self):
        assert A.resolve_article_ids(["99.9.XX.1"]) == ["99.9.XX.1"]

    def test_graph_traverse_accepts_codes(self, fake_retriever):
        run("graph_traverse", {"mapcs": ["20.2.LQ.137"]}, fake_retriever)
        assert fake_retriever.calls[0][1] == ("M_THAISAN",)

    def test_get_full_article_accepts_code(self, fake_retriever, fake_mysql):
        payload, hits = run("get_full_article", {"mapc": "8.4.LQ.8"}, fake_retriever)
        assert payload["ten"] == "Điều kiện kết hôn" and hits[0]["mapc"] == "M_KETHON"
