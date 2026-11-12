"""Bộ chấm của eval gọi tool (evaluation/eval_tool_calls.py) — không gọi API."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evaluation"))
import eval_tool_calls as E  # noqa: E402

import services.agentic_rag as A  # noqa: E402
from tests.conftest import FakeNeo4jDriver, assistant, tool_call  # noqa: E402


def call(name, args, result_ids=()):
    return {"name": name, "args": args, "result": json.dumps([{"mapc": m} for m in result_ids])}


def trace(calls, answer="", forced=False, iterations=2, max_iterations=6):
    return {"calls": calls, "answer": answer, "forced_web": forced,
            "iterations": iterations, "max_iterations": max_iterations}


CASE = {"id": "c", "category": "muc_phat", "query": "q",
        "must_call": ["vector_search", "web_search"], "must_not_call": ["get_full_article"],
        "first_tool": "vector_search", "max_tool_calls": 3, "answer_must_contain": ["xe đạp máy"]}


class TestLoadCases:
    def test_shipped_cases_are_valid(self):
        cases = E.load_cases(os.path.join(E.BASE, "tool_call_cases.json"))
        assert len(cases) >= 20 and len({c["category"] for c in cases}) >= 5

    @pytest.mark.parametrize("bad, msg", [
        ([{"id": "a", "category": "x"}], "thiếu"),
        ([{"id": "a", "category": "x", "query": "q"}] * 2, "trùng id"),
        ([{"id": "a", "category": "x", "query": "q", "must_call": ["search_google"]}], "không tồn tại"),
        ([{"id": "a", "category": "x", "query": "q", "must_call": ["web_search"],
           "must_not_call": ["web_search"]}], "vừa must_call"),
        ([{"id": "a", "category": "x", "query": "q", "must_cal": []}], "khoá lạ"),
    ])
    def test_rejects_bad_cases(self, tmp_path, bad, msg):
        p = tmp_path / "c.json"
        p.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValueError, match=msg):
            E.load_cases(str(p))


class TestScoreCase:
    def test_pass(self):
        r = E.score_case(CASE, trace([call("vector_search", {"query": "a"}, ["M1"]),
                                      call("web_search", {"query": "b"})], answer="Xe đạp máy bị phạt"))
        assert r["pass"] and r["must_recall"] == 1.0 and not r["forbidden"]

    def test_missing_required_tool(self):
        r = E.score_case(CASE, trace([call("vector_search", {"query": "a"})], answer="xe đạp máy"))
        assert not r["pass"] and r["must_recall"] == 0.5 and r["checks"]["must_call"] is False

    def test_forbidden_tool(self):
        r = E.score_case(CASE, trace([call("vector_search", {"query": "a"}, ["M1"]), call("web_search", {"query": "b"}),
                                      call("get_full_article", {"mapc": "M1"})], answer="xe đạp máy"))
        assert r["forbidden"] and not r["checks"]["must_not_call"]

    def test_wrong_first_tool(self):
        r = E.score_case(CASE, trace([call("web_search", {"query": "b"}), call("vector_search", {"query": "a"})],
                                     answer="xe đạp máy"))
        assert r["checks"]["first_tool"] is False

    def test_over_budget(self):
        calls = [call("vector_search", {"query": str(i)}) for i in range(3)] + [call("web_search", {"query": "w"})]
        assert E.score_case(CASE, trace(calls, answer="xe đạp máy"))["checks"]["budget"] is False

    def test_answer_keywords_case_insensitive(self):
        calls = [call("vector_search", {"query": "a"}), call("web_search", {"query": "b"})]
        assert E.score_case(CASE, trace(calls, answer="XE ĐẠP MÁY"))["checks"]["answer"]
        assert not E.score_case(CASE, trace(calls, answer="xe đạp"))["checks"]["answer"]

    def test_answer_any(self):
        case = {"id": "t", "category": "x", "query": "q", "answer_must_contain_any": ["2.000.000", "2 triệu"]}
        assert E.score_case(case, trace([], answer="dưới 2 triệu đồng"))["checks"]["answer"]
        assert not E.score_case(case, trace([], answer="dưới 1 triệu"))["checks"]["answer"]

    def test_hallucinated_mapc_detected(self):
        # graph_traverse dùng mã "MX" chưa từng xuất hiện trong kết quả trước → agent bịa
        r = E.score_case({"id": "t", "category": "x", "query": "q"}, trace([
            call("vector_search", {"query": "a"}, ["M1", "M2"]),
            call("graph_traverse", {"mapcs": ["M1"]}, ["M3"]),
            call("get_full_article", {"mapc": "M3"}),
            call("get_full_article", {"mapc": "MX"}),
        ]))
        assert (r["id_calls"], r["invalid_id_calls"]) == (3, 1)

    def test_empty_and_redundant_args(self):
        r = E.score_case({"id": "t", "category": "x", "query": "q"}, trace([
            call("vector_search", {"query": " "}), call("graph_traverse", {"mapcs": []}),
            call("web_search", {"query": "a"}), call("web_search", {"query": "a"}),
        ]))
        assert r["empty_args"] == 2 and r["redundant"] == 1

    def test_forced_web_expectation(self):
        case = {"id": "t", "category": "x", "query": "q", "expect_forced_web": False}
        assert not E.score_case(case, trace([], forced=True))["checks"]["forced_web"]
        assert E.score_case({**case, "expect_forced_web": None}, trace([], forced=True))["checks"]["forced_web"]

    def test_max_iterations_flag(self):
        assert E.score_case({"id": "t", "category": "x", "query": "q"},
                            trace([], iterations=6, max_iterations=6))["hit_max_iter"]


def test_aggregate():
    rows = [E.score_case(CASE, trace([call("vector_search", {"query": "a"}, ["M1"]),
                                      call("web_search", {"query": "b"})], answer="xe đạp máy")),
            E.score_case(CASE, trace([call("vector_search", {"query": "a"})], answer=""))]
    a = E.aggregate(rows)
    assert a["cases"] == 2 and a["case_pass"] == 0.5 and a["must_call_recall"] == 0.75
    assert a["avg_calls"] == 1.5 and a["invalid_id_rate"] is None and a["should_call_recall"] is None


def test_run_case_end_to_end(fake_retriever, fake_mysql, scripted_client):
    """Chạy harness với agent thật + LLM giả: ghi đúng chuỗi tool, phát hiện buộc web, tự khôi phục patch."""
    fake_retriever.fused = [{"mapc": "M_XEDAP", "score": 0.9}]
    client = scripted_client([
        assistant(tool_calls=[tool_call("vector_search", {"query": "mũ bảo hiểm xe đạp"})]),
        assistant("nháp"),
        assistant(tool_calls=[tool_call("web_search", {"query": "mức phạt 2025"}, "c2")]),
        assistant("Xe đạp máy bị phạt; xe đạp thường thì không."),
    ])
    case = {"id": "scope_helmet", "category": "pham_vi_ap_dung",
            "query": "Đi xe đạp không đội mũ bảo hiểm có bị xử phạt không?",
            "must_call": ["vector_search", "web_search"], "first_tool": "vector_search",
            "expect_forced_web": True, "answer_must_contain": ["xe đạp máy"]}
    orig = (A.execute_agent_tool, A.web_verify_instruction)
    tr = E.run_case(case, agent_module=A, openai_client=client, retriever=fake_retriever,
                    neo4j_driver=FakeNeo4jDriver(), max_iterations=6, model="gpt-4.1")
    assert (A.execute_agent_tool, A.web_verify_instruction) == orig      # patch đã gỡ
    assert [c["name"] for c in tr["calls"]] == ["vector_search", "web_search"]
    assert tr["forced_web"] and "mức phạt" in tr["forced_reason"]
    assert E.score_case(case, tr)["pass"]
