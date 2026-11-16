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
        assistant(tool_calls=[tool_call("web_search", {"query": "kết hôn"}, "c2")]),
        assistant("trả lời từ web"),
    ])
    assert out["answer"] == "trả lời từ web"
    assert client.requests[1]["tool_choice"] == WEB_SEARCH_TOOL_CHOICE   # buộc ngay sau vòng tra rỗng


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
        assistant(tool_calls=[tool_call("web_search", {"query": "mức phạt mũ bảo hiểm 2025"}, "call_2")]),
        assistant("Câu trả lời cuối có kiểm tra văn bản mới"),
    ])
    assert out["answer"] == "Câu trả lời cuối có kiểm tra văn bản mới"
    # Buộc web NGAY lượt sau vòng tool — model chưa kịp viết bản nháp (trước đây: viết nháp rồi bỏ)
    assert client.requests[1]["tool_choice"] == WEB_SEARCH_TOOL_CHOICE
    assert client.requests[2]["tool_choice"] == "auto"
    assert len(client.requests) == 3
    msgs = client.requests[1]["messages"]
    assert msgs[-1]["role"] == "system" and "web_search" in msgs[-1]["content"]


def test_web_verification_only_once(agent, fake_retriever):
    fake_retriever.fused = [{"mapc": "M_XEDAP", "score": 0.9}]
    out, client = agent("mức phạt vượt đèn đỏ", [
        assistant(tool_calls=[tool_call("vector_search", {"query": "vượt đèn đỏ"})]),
        assistant(tool_calls=[tool_call("web_search", {"query": "vượt đèn đỏ"}, "call_2")]),
        assistant(tool_calls=[tool_call("vector_search", {"query": "đèn tín hiệu"}, "call_3")]),
        assistant("trả lời"),
    ])
    assert out["answer"] == "trả lời" and len(client.requests) == 4
    assert [r["tool_choice"] for r in client.requests].count(WEB_SEARCH_TOOL_CHOICE) == 1


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
        assistant(tool_calls=[tool_call("web_search", {"query": "x"}, "c2")]),   # đã tra mà rỗng → buộc web
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


# ── Stream thật + sự kiện (agent_events) ─────────────────────────────────────
from types import SimpleNamespace as NS  # noqa: E402


def chunk(content=None, tool_calls=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tool_calls))])


def tc_delta(index, id=None, name=None, args=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))


class StreamingClient:
    """chat.completions.create(stream=True) trả lần lượt các danh sách chunk đã soạn."""

    def __init__(self, turns):
        self.turns, self.requests = list(turns), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        assert kw.get("stream") is True
        self.requests.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
        return iter(self.turns.pop(0))


def events(client, fake_retriever, question="Điều kiện kết hôn là gì", **kw):
    return list(A.agent_events(openai_client=client, retriever=fake_retriever, neo4j_driver=FakeNeo4jDriver(),
                               user_prompt=question, history=[], stream=True, **kw))


def test_stream_tokens_arrive_incrementally(fake_retriever, fake_mysql, monkeypatch):
    monkeypatch.setattr(A, "STREAM_HOLD_CHARS", 10)
    fake_retriever.fused = [{"mapc": "M_KETHON", "score": 0.95}]
    client = StreamingClient([
        # tool call bị cắt nhỏ qua nhiều chunk (id/tên/đối số)
        [chunk(tool_calls=[tc_delta(0, id="c1", name="vector_", args='{"query": "kết')]),
         chunk(tool_calls=[tc_delta(0, name="search", args=' hôn"}')])],
        [chunk("Nam từ đủ "), chunk("20 tuổi, "), chunk("nữ từ đủ 18 tuổi.")],
    ])
    evs = events(client, fake_retriever)
    types = [e["type"] for e in evs]
    assert types[:2] == ["tool", "tool_done"] and evs[0]["args"] == {"query": "kết hôn"}
    assert evs[0]["tool"] == "vector_search"
    first_tok = types.index("token")
    assert types[first_tok - 1] == "compose" and evs[first_tok - 1]["passage_hits"][0]["mapc"] == "M_KETHON"
    toks = [e["text"] for e in evs if e["type"] == "token"]
    assert len(toks) >= 2 and "".join(toks) == "Nam từ đủ 20 tuổi, nữ từ đủ 18 tuổi."   # nhiều mảnh, không gom 1 cục
    assert evs[-1] == {"type": "final", "answer": "Nam từ đủ 20 tuổi, nữ từ đủ 18 tuổi.",
                       "passage_hits": evs[-1]["passage_hits"], "iterations": 2}


def test_stream_preamble_before_tool_call_not_shown(fake_retriever, fake_mysql):
    fake_retriever.fused = [{"mapc": "M_KETHON", "score": 0.95}]
    client = StreamingClient([
        [chunk("Tôi sẽ tra cứu."), chunk(tool_calls=[tc_delta(0, id="c1", name="vector_search", args="{}")])],
        [chunk("Trả lời.")],
    ])
    evs = events(client, fake_retriever)
    assert [e["text"] for e in evs if e["type"] == "token"] == ["Trả lời."]


def test_stream_forced_web_event_before_any_token(fake_retriever, fake_mysql):
    fake_retriever.fused = [{"mapc": "M_XEDAP", "score": 0.9}]
    client = StreamingClient([
        [chunk(tool_calls=[tc_delta(0, id="c1", name="vector_search", args='{"query": "mũ"}')])],
        [chunk(tool_calls=[tc_delta(0, id="c2", name="web_search", args='{"query": "mũ 2025"}')])],
        [chunk("Câu trả lời đã kiểm tra web.")],
    ])
    evs = events(client, fake_retriever, question="Không đội mũ bảo hiểm bị phạt bao nhiêu")
    types = [e["type"] for e in evs]
    assert types.index("verify") < types.index("token")
    assert client.requests[1]["tool_choice"] == WEB_SEARCH_TOOL_CHOICE


def test_stream_synthesis_after_max_iterations(fake_retriever, fake_mysql):
    fake_retriever.fused = [{"mapc": "M_KETHON", "score": 0.95}]
    loop = [[chunk(tool_calls=[tc_delta(0, id=f"c{i}", name="vector_search", args='{"query": "x"}')])]
            for i in range(2)]
    client = StreamingClient(loop + [[chunk("Tổng "), chunk("hợp")]])
    evs = events(client, fake_retriever, max_iterations=2)
    assert evs[-1]["answer"] == "Tổng hợp" and evs[-1]["iterations"] == 2
    assert "tools" not in client.requests[-1]
