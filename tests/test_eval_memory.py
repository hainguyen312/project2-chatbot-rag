"""Mục 3: bộ chấm + harness của evaluation/eval_memory.py (LLM giả, không gọi API)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evaluation"))
import eval_memory as E  # noqa: E402

from tests.memory_fakes import make_manager  # noqa: E402


def mem(fact, t="core"):
    return {"fact": fact, "mem_type": t}


def test_shipped_cases_valid():
    cases = E.load_cases(os.path.join(E.BASE, "memory_cases.json"))
    assert len(cases) >= 12 and {"trich_xuat", "nhay_cam", "cap_nhat", "truy_xuat"} <= {c["category"] for c in cases}


@pytest.mark.parametrize("bad, msg", [
    ([{"id": "a", "category": "x", "turns": ["t"]}], "không có kỳ vọng"),
    ([{"id": "a", "category": "x", "turns": ["t"], "expect": {"contain": ["x"]}}], "khoá expect lạ"),
    ([{"id": "a", "category": "x", "turns": ["t"], "expect": {"types": {"x": "hồ sơ"}}}], "loại memory"),
    ([{"id": "a", "category": "x", "turns": ["t"], "expect": {"max_matching": [{"pattern": "(", "max": 1}]}}], "regex"),
])
def test_rejects_bad_cases(tmp_path, bad, msg):
    p = tmp_path / "c.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match=msg):
        E.load_cases(str(p))


class TestScore:
    def test_contains_and_types(self):
        case = {"id": "c", "category": "x", "expect": {"contains": ["2003"], "types": {"2003": "core"}}}
        assert E.score_case(case, [mem("Người dùng sinh năm 2003")], {})["pass"]
        assert not E.score_case(case, [mem("Người dùng sinh năm 2003", "episodic")], {})["pass"]

    def test_sensitive_leak(self):
        case = {"id": "c", "category": "x", "expect": {"not_contains": ["001203012345"]}}
        assert not E.score_case(case, [mem("CCCD 001203012345")], {})["pass"]

    def test_duplicate_detected(self):
        case = {"id": "c", "category": "x", "expect": {"max_matching": [{"pattern": "2003", "max": 1}]}}
        assert not E.score_case(case, [mem("sinh năm 2003"), mem("năm sinh 2003")], {})["pass"]

    def test_no_type_and_max_total(self):
        case = {"id": "c", "category": "x", "expect": {"max_total": 1, "no_type": ["core"]}}
        assert E.score_case(case, [mem("quan tâm luật hôn nhân", "semantic")], {})["pass"]
        assert not E.score_case(case, [mem("a", "semantic"), mem("b", "semantic")], {})["pass"]

    def test_retrieval_check(self):
        case = {"id": "c", "category": "x", "queries": [{"q": "đủ tuổi kết hôn?", "must_include": ["2003"]}]}
        assert E.score_case(case, [], {"đủ tuổi kết hôn?": [{"fact": "sinh năm 2003"}]})["pass"]
        assert not E.score_case(case, [], {"đủ tuổi kết hôn?": []})["pass"]


def test_run_case_end_to_end_and_cleans_up():
    mm = make_manager()
    mm.client.queues["memory_facts"] += [
        {"facts": [{"fact": "Người dùng học tại Bách Khoa", "type": "core"}]},
        {"facts": [{"fact": "Người dùng chuyển sang học Kinh tế Quốc dân", "type": "core"}]},
    ]
    mm.client.queues["memory_decisions"].append(
        {"decisions": [{"candidate": 0, "action": "UPDATE", "target_id": 1,
                        "merged_fact": "Người dùng chuyển từ Bách Khoa sang Kinh tế Quốc dân", "reason": "đổi trường"}]})
    case = {"id": "update_school", "category": "cap_nhat", "turns": ["…", "…"],
            "expect": {"contains": ["Kinh tế Quốc dân"], "max_matching": [{"pattern": "Kinh tế Quốc dân", "max": 1}]},
            "queries": [{"q": "tôi học trường nào", "must_include": ["Kinh tế Quốc dân"]}]}
    r = E.run_case(case, mm, user_id="eval-mem-test")
    assert r["pass"] and r["actions"] == ["ADD", "UPDATE"]
    assert mm.mongo_col.docs == []       # dữ liệu eval đã xoá


def test_summarize():
    rows = [{"pass": True, "checks": {"contains:a": True, "not_contains:b": True}, "actions": ["ADD"], "latency_s": 1},
            {"pass": False, "checks": {"not_contains:b": False, "max_matching:x": False}, "actions": ["UPDATE"], "latency_s": 3}]
    s = E.summarize(rows)
    assert s["case_pass"] == 0.5 and s["sensitive_leaks"] == 1 and s["duplicate_or_stale"] == 1
    assert s["by_check"]["not_contains"] == 0.5 and s["actions"] == {"ADD": 1, "UPDATE": 1}
