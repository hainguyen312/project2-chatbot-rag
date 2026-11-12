"""Vòng lặp ReAct (run_agentic_rag_sync) với OpenAI giả theo kịch bản + luật buộc kiểm tra web."""
import pytest

import services.agentic_rag as A
from services.agentic_rag import WEB_SEARCH_TOOL_CHOICE, run_agentic_rag_sync, web_verify_reason
from tests.conftest import FakeNeo4jDriver, assistant, tool_call

PD = lambda score: {"source": "phapdien", "score": score}  # noqa: E731
WEB = {"source": "web", "score": 0.7}


# ── web_verify_reason: khi nào buộc kiểm tra web ─────────────────────────────
class TestWebVerifyReason:
    @pytest.mark.parametrize("q", [
        "Đi xe đạp không đội mũ bảo hiểm có bị xử phạt không?",
        "Mức phạt nồng độ cồn xe máy",
        "Lệ phí làm căn cước bao nhiêu",
        "Lương tối thiểu vùng hiện nay",
        "Quy định năm 2025 về thuế thu nhập cá nhân",
    ])
    def test_time_sensitive_questions(self, q):
        assert "mức phạt" in web_verify_reason(q, [PD(0.95)], set())

    @pytest.mark.parametrize("q", [
        "Điều kiện để được kết hôn là gì",
        "Người 17 tuổi có được ký hợp đồng lao động không",
        "Phiên toà sơ thẩm diễn ra thế nào",           # "phí" chỉ khớp nguyên từ, không khớp "Phiên"
    ])
    def test_stable_questions_with_good_retrieval(self, q):
        assert web_verify_reason(q, [PD(0.9)], set()) is None

    def test_low_retrieval_score(self):
        assert "thấp" in web_verify_reason("điều kiện kết hôn", [PD(0.3), PD(0.45)], set())

    def test_searched_but_no_phapdien_results(self):
        assert "không tìm thấy" in web_verify_reason("điều kiện kết hôn", [WEB], {"vector_search"})

    def test_no_retrieval_at_all_is_not_forced(self):
        # Agent trả lời không cần tra cứu (chào hỏi, câu chỉ dùng memory) → không ép gọi web
        assert web_verify_reason("điều kiện kết hôn", [], set()) is None

    def test_not_again_after_web_search(self):
        assert web_verify_reason("mức phạt", [PD(0.2)], {"web_search"}) is None

    def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setattr(A, "AGENT_AUTO_WEB_VERIFY", False)
        assert web_verify_reason("mức phạt", [], set()) is None


# ── Vòng lặp ─────────────────────────────────────────────────────────────────
@pytest.fixture
def agent(fake_retriever, fake_mysql, scripted_client):
    """Chạy agent với kịch bản LLM; trả (kết quả, client) để assert request đã gửi."""
    def _run(question, responses, max_iterations=6):
        client = scripted_client(responses)
        out = run_agentic_rag_sync(openai_client=client, retriever=fake_retriever,
                                   neo4j_driver=FakeNeo4jDriver(), user_prompt=question,
                                   history=[], max_iterations=max_iterations)
        return out, client
    return _run


def tool_msgs(client, i):
    return [m for m in client.requests[i]["messages"] if m["role"] == "tool"]


def test_direct_answer_no_tools(agent):
    out, client = agent("xin chào", [assistant("Chào bạn")])
    assert out == {"answer": "Chào bạn", "passage_hits": [], "iterations": 1}
    assert client.requests[0]["tool_choice"] == "auto" and client.requests[0]["tools"] == A.RAG_AGENT_TOOLS


def test_searched_nothing_forces_web(agent, fake_retriever):
    fake_retriever.fused = []
    out, client = agent("điều kiện kết hôn", [
        assistant(tool_calls=[tool_call("vector_search", {"query": "kết hôn"})]),
        assistant("không biết"),
        assistant(tool_calls=[tool_call("web_search", {"query": "kết hôn"}, "c2")]),
        assistant("trả lời từ web"),
    ])
    assert out["answer"] == "trả lời từ web"
    assert client.requests[2]["tool_choice"] == WEB_SEARCH_TOOL_CHOICE


def test_tool_then_answer(agent, fake_retriever):
    fake_retriever.fused = [{"mapc": "M_KETHON", "score": 0.95}]
    out, client = agent("Điều kiện kết hôn là gì", [
        assistant(tool_calls=[tool_call("vector_search", {"query": "điều kiện kết hôn"})]),
        assistant("Nam từ đủ 20 tuổi, nữ từ đủ 18 tuổi (Điều 8 Luật 52/2014/QH13)."),
    ])
    assert out["iterations"] == 2 and out["answer"].startswith("Nam từ đủ 20")
    assert [h["mapc"] for h in out["passage_hits"]] == ["M_KETHON"]
    # kết quả tool được trả về cho LLM đúng tool_call_id
    assert tool_msgs(client, 1)[0]["tool_call_id"] == "call_1"
    assert all(r["tool_choice"] == "auto" for r in client.requests)   # không bị buộc web


def test_forces_web_search_for_penalty_question(agent, fake_retriever):
    fake_retriever.fused = [{"mapc": "M_XEDAP", "score": 0.9}]
    out, client = agent("Đi xe đạp không đội mũ bảo hiểm có bị xử phạt không?", [
        assistant(tool_calls=[tool_call("vector_search", {"query": "mũ bảo hiểm xe đạp"})]),
        assistant("Bản nháp chưa kiểm tra web"),                       # bị chặn lại
        assistant(tool_calls=[tool_call("web_search", {"query": "mức phạt mũ bảo hiểm 2025"}, "call_2")]),
        assistant("Câu trả lời cuối có kiểm tra văn bản mới"),
    ])
    assert out["answer"] == "Câu trả lời cuối có kiểm tra văn bản mới"
    assert client.requests[2]["tool_choice"] == WEB_SEARCH_TOOL_CHOICE   # vòng sau bị buộc gọi web
    assert client.requests[3]["tool_choice"] == "auto"                   # rồi trả lại auto
    msgs = client.requests[2]["messages"]
    assert msgs[-1]["role"] == "system" and "web_search" in msgs[-1]["content"]
    assert not any(m.get("content") == "Bản nháp chưa kiểm tra web" for m in msgs)  # bản nháp bị bỏ


def test_web_verification_only_once(agent, fake_retriever):
    fake_retriever.fused = [{"mapc": "M_XEDAP", "score": 0.9}]
    out, client = agent("mức phạt vượt đèn đỏ", [
        assistant(tool_calls=[tool_call("vector_search", {"query": "vượt đèn đỏ"})]),
        assistant("nháp 1"),
        assistant(tool_calls=[tool_call("web_search", {"query": "vượt đèn đỏ"}, "call_2")]),
        assistant("trả lời"),
    ])
    assert out["answer"] == "trả lời" and len(client.requests) == 4


def test_no_double_web_when_agent_already_searched(agent, fake_retriever):
    fake_retriever.fused = [{"mapc": "M_XEDAP", "score": 0.9}]
    out, client = agent("mức phạt vượt đèn đỏ", [
        assistant(tool_calls=[tool_call("vector_search", {"query": "a"}),
                              tool_call("web_search", {"query": "b"}, "call_2")]),
        assistant("trả lời"),
    ])
    assert out["answer"] == "trả lời" and len(client.requests) == 2


def test_no_forced_web_on_last_iteration(agent, fake_retriever):
    fake_retriever.fused = [{"mapc": "M_XEDAP", "score": 0.9}]
    out, client = agent("mức phạt", [
        assistant(tool_calls=[tool_call("vector_search", {"query": "a"})]),
        assistant("trả lời ở vòng cuối"),
    ], max_iterations=2)
    assert out["answer"] == "trả lời ở vòng cuối"


def test_max_iterations_then_synthesis(agent, fake_retriever):
    fake_retriever.fused = [{"mapc": "M_KETHON", "score": 0.95}]
    loop = [assistant(tool_calls=[tool_call("vector_search", {"query": f"q{i}"}, f"c{i}")]) for i in range(3)]
    out, client = agent("điều kiện kết hôn", loop + [assistant("tổng hợp")], max_iterations=3)
    assert out["answer"] == "tổng hợp" and out["iterations"] == 3
    assert "tools" not in client.requests[-1]                       # lượt tổng hợp không kèm tool


def test_malformed_tool_arguments(agent, fake_retriever):
    out, client = agent("điều kiện kết hôn", [
        assistant(tool_calls=[tool_call("vector_search", "{không phải json")]),
        assistant("nháp"),                                               # đã tra mà rỗng → buộc web
        assistant(tool_calls=[tool_call("web_search", {"query": "x"}, "c2")]),
        assistant("ok"),
    ])
    assert out["answer"] == "ok"
    assert tool_msgs(client, 1)[0]["content"] == "[]"                # query rỗng → [] , không crash


def test_unknown_tool_reported_to_llm(agent):
    out, client = agent("q", [
        assistant(tool_calls=[tool_call("xoa_du_lieu", {})]),
        assistant("ok"),
    ])
    assert "Unknown tool" in tool_msgs(client, 1)[0]["content"]


def test_history_and_memory_injected(fake_retriever, fake_mysql, scripted_client):
    client = scripted_client([assistant("ok")])
    hist = [{"role": "user", "content": f"u{i}"} for i in range(12)]
    run_agentic_rag_sync(openai_client=client, retriever=fake_retriever, neo4j_driver=FakeNeo4jDriver(),
                         user_prompt="q", history=hist, system_prompt_extra="NĂM SINH 2003")
    msgs = client.requests[0]["messages"]
    assert "NĂM SINH 2003" in msgs[0]["content"]
    assert len([m for m in msgs if m["role"] == "user"]) == 8 + 1      # 8 lượt lịch sử gần nhất + câu hỏi


def test_system_prompt_has_citation_and_scope_rules():
    p = A.agent_system_prompt(situation_mode=False)
    assert "vbqppl" in p and "ma_phap_dien" in p
    assert "ĐỐI CHIẾU PHẠM VI ÁP DỤNG" in p
