"""services/evalhub: registry + chuẩn hoá kết quả + kho lần chạy + runner (không gọi dịch vụ thật)."""
from __future__ import annotations

import json
import sys
import time

import pytest

from services.evalhub import runner as runner_mod
from services.evalhub import store
from services.evalhub.suites import (
    SUITES, SUITES_BY_ID, MetricSpec, Param, Suite, coerce_params, norm_exceptions, norm_memory, norm_retrieval,
    norm_tool_calls, pytest_postprocess, suite_for_file,
)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    res, runs = tmp_path / "results", tmp_path / "runs"
    res.mkdir()
    monkeypatch.setattr(store, "RESULTS_DIR", str(res))
    monkeypatch.setattr(store, "RUNS_DIR", str(runs))
    store._cache.clear()
    return res, runs


# ------------------------------------------------------------------ registry / tham số
def test_registry_ids_and_prefixes_unique():
    assert len({s.id for s in SUITES}) == len(SUITES)
    prefixes = [s.file_prefix for s in SUITES]
    assert not any(a != b and b.startswith(a) for a in prefixes for b in prefixes)
    assert suite_for_file("tool_eval_gpt-4.1_20261116_144452.json").id == "tool_calls"
    assert suite_for_file("judgments_cache.json") is None
    assert suite_for_file("thesis_snapshot.json") is None


def test_coerce_params_types_and_rejects():
    s = SUITES_BY_ID["tool_calls"]
    out = coerce_params(s, {"model": "gpt-4o-mini", "repeat": "3"})
    assert out["repeat"] == 3 and out["max_iterations"] == 6 and out["only"] is None
    with pytest.raises(ValueError, match="không hợp lệ"):
        coerce_params(s, {"cases": "/etc/passwd"})           # tham số không khai báo
    with pytest.raises(ValueError):
        coerce_params(s, {"model": "gpt-9"})                 # ngoài danh sách
    with pytest.raises(ValueError):
        coerce_params(s, {"repeat": 99})                     # vượt max
    with pytest.raises(ValueError):
        coerce_params(s, {"only": "--evil"})                 # chuỗi giống cờ CLI


def test_build_argv_flags_and_out():
    s = SUITES_BY_ID["retrieval"]
    argv = s.build_argv(coerce_params(s, {"skip_judge": True, "synthetic": 50, "dry_run": False}), "/x/out.json")
    assert argv[0] == sys.executable and argv[1] == "evaluation/eval_retrieval_nogt.py"
    assert "--skip-judge" in argv and "--dry-run" not in argv
    assert argv[argv.index("--synthetic") + 1] == "50"
    assert "--limit" not in argv                              # None → bỏ qua
    assert argv[-2:] == ["--out", "/x/out.json"]
    t = SUITES_BY_ID["unit_tests"]
    argv = t.build_argv({"k": "roles"}, "/x/p.json")
    assert argv[argv.index("-k") + 1] == "roles" and argv[-1] == "--junitxml=/x/p.json.raw"


# ------------------------------------------------------------------ adapter
def test_norm_tool_calls_repeats_and_traces():
    raw = {"model": "gpt-4.1", "summary": {"case_pass": 0.5, "cost_usd": 0.1},
           "rows": [{"id": "a", "category": "c", "pass": True, "tools": ["vector_search"], "checks": {}},
                    {"id": "a", "category": "c", "pass": False, "tools": [], "checks": {"x": False}}],
           "traces": [{"case": "a", "rep": 0, "answer": "A0"}, {"case": "a", "rep": 1, "answer": "A1"}]}
    n = norm_tool_calls(raw)
    assert n["variant"] == "gpt-4.1" and n["metrics"]["case_pass"] == 0.5
    rows = n["groups"][0]["rows"]
    assert [r["id"] for r in rows] == ["a", "a#2"]
    assert rows[1]["detail"]["answer"] == "A1" and rows[1]["cells"]["tools"] == "—"


def test_norm_memory_flattens_actions_and_failed_checks():
    raw = {"summary": {"case_pass": 1.0, "by_check": {"type": 1.0}, "actions": {"ADD": 2}},
           "rows": [{"id": "p", "category": "c", "pass": False, "checks": {"contains:x": False, "type:y": True},
                     "actions": ["ADD"], "facts": ["f"]}]}
    n = norm_memory(raw)
    assert n["metrics"]["actions.ADD"] == 2 and n["metrics"]["by_check.type"] == 1.0
    assert n["groups"][0]["rows"][0]["cells"]["failed"] == "contains:x"


def test_norm_retrieval_tables_and_known_item_rows():
    raw = {"config": {"k": 10, "synthetic": 2},
           "summary": {"hybrid_new": {"nDCG@10": 0.6}, "bm25_only": {"nDCG@10": 0.65}},
           "per_query": {"hybrid_new": {"q1": {"nDCG@10": 0.5}}, "bm25_only": {"q1": {"nDCG@10": 0.7}}},
           "known_item": {"hybrid_new": {"Hit@1": 0.5}},
           "known_item_ranks": {"hybrid_new": [1, None], "bm25_only": [3, 2]}}
    n = norm_retrieval(raw)
    assert n["metrics"]["judge.hybrid_new.nDCG@10"] == 0.6 and n["metrics"]["known_item.hybrid_new.Hit@1"] == 0.5
    assert len(n["tables"]) == 2
    judge, ki = n["groups"]
    assert judge["rows"][0]["cells"] == {"hybrid_new": 0.5, "bm25_only": 0.7}
    assert [r["pass"] for r in ki["rows"]] == [True, False]


def test_norm_exceptions_and_bad_input_does_not_crash(dirs):
    n = norm_exceptions({"retrieval": {"summary": {"A": 0.5, "n": "x"}, "rows": [{"cau_hoi": "q", "b_on": True}]},
                         "claude_cost_usd": 0.2})
    assert n["metrics"]["retrieval.A"] == 0.5 and n["metrics"]["cost.total_usd"] == 0.2
    assert "retrieval.n" not in n["metrics"]                                   # giá trị không phải số bị bỏ
    assert n["groups"][0]["rows"][0]["pass"] is True
    # File kết quả lệch định dạng → vẫn liệt kê, kèm parse_error
    res, _ = dirs
    (res / "tool_eval_x_20260101_000000.json").write_text(json.dumps({"rows": [{"no_id": 1}]}), encoding="utf-8")
    run = store.get_run("tool_eval_x_20260101_000000")
    assert run["error"] and run["status"] == "done"


def test_pytest_postprocess(tmp_path):
    xml = tmp_path / "r.xml"
    xml.write_text(
        '<testsuites><testsuite>'
        '<testcase classname="tests.test_a" name="t1" time="0.1"/>'
        '<testcase classname="tests.test_a.TestX" name="t2" time="0.2"><failure message="boom">tb</failure></testcase>'
        '<testcase classname="tests.test_b" name="t3"><skipped message="s"/></testcase>'
        '</testsuite></testsuites>', encoding="utf-8")
    out = tmp_path / "r.json"
    pytest_postprocess(str(xml), str(out))
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["summary"]["tests"] == 3 and d["summary"]["failed"] == 1 and d["summary"]["pass_rate"] == 0.5
    assert [r["file"] for r in d["rows"]] == ["tests/test_a.py", "tests/test_a.py", "tests/test_b.py"]
    assert not xml.exists()


# ------------------------------------------------------------------ store
def test_store_lists_cli_and_ui_runs(dirs):
    res, _ = dirs
    (res / "memory_eval_20260101_000000.json").write_text(json.dumps({"summary": {"case_pass": 1.0}, "rows": []}), encoding="utf-8")
    (res / "judgments_cache.json").write_text("{}", encoding="utf-8")
    store.save_meta({"id": "memory_eval_20260102_000000", "suite": "memory", "status": "running",
                     "created_at": "2026-01-02T00:00:00", "params": {}})
    runs = store.list_runs()
    assert [r["id"] for r in runs] == ["memory_eval_20260102_000000", "memory_eval_20260101_000000"]
    assert runs[0]["status"] == "running" and runs[0]["source"] == "ui" and runs[0]["metrics"] == {}
    assert runs[1]["source"] == "cli" and runs[1]["metrics"]["case_pass"] == 1.0
    store.update_label("memory_eval_20260101_000000", "mốc")
    r = store.get_run("memory_eval_20260101_000000")
    assert r["label"] == "mốc" and r["source"] == "cli"
    with pytest.raises(KeyError):
        store.get_run("../secret")


# ------------------------------------------------------------------ runner
def _fake_suite(code: str) -> Suite:
    return Suite(id="fake", name="Fake", description="", kind="test", file_prefix="fake_",
                 script=["-c", code], params=[Param("n", "N", "int", 1)],
                 metrics=[MetricSpec("x", "X")], normalize=lambda raw: {"metrics": raw})


def _wait(run_id: str, timeout: float = 20) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        m = store.load_meta(run_id)
        if m and m["status"] not in ("queued", "running"):
            return m
        time.sleep(0.1)
    raise AssertionError("runner không kết thúc")


@pytest.fixture
def fake(monkeypatch, dirs):
    def install(code: str):
        s = _fake_suite(code)
        monkeypatch.setitem(SUITES_BY_ID, "fake", s)
        monkeypatch.setattr(runner_mod, "SUITES_BY_ID", SUITES_BY_ID)
        monkeypatch.setattr(store, "suite_for_file", lambda name: s if name.startswith("fake_") else None)
        return runner_mod.LocalRunner(max_concurrent=1)
    return install


def test_runner_success_writes_result(fake):
    code = "import sys,json; json.dump({'x': int(sys.argv[2])}, open(sys.argv[-1],'w')); print('hello')"
    r = fake(code)
    meta = r.submit("fake", {"n": 7}, "a@b")
    done = _wait(meta["id"])
    assert done["status"] == "done" and done["started_by"] == "a@b"
    assert store.get_run(meta["id"])["metrics"] == {"x": 7}
    assert "hello" in store.read_log(meta["id"])


def test_runner_failure_without_result(fake):
    r = fake("import sys; print('kaboom'); sys.exit(3)")
    done = _wait(r.submit("fake", {}, "a@b")["id"])
    assert done["status"] == "failed" and done["exit_code"] == 3 and "kaboom" in done["error"]


def test_runner_cancel_and_recover(fake):
    r = fake("import time; time.sleep(30)")
    m = r.submit("fake", {}, "a@b")
    time.sleep(0.5)
    r.cancel(m["id"])
    assert _wait(m["id"])["status"] == "cancelled"
    store.save_meta({"id": "fake_x", "suite": "fake", "status": "running"})
    assert r.recover() == 1 and store.load_meta("fake_x")["status"] == "interrupted"


# ------------------------------------------------------------------ chi phí / bảng giá / ước tính
from services.evalhub import estimate as estimate_mod  # noqa: E402
from services.evalhub import pricing  # noqa: E402


@pytest.fixture
def prices(tmp_path, monkeypatch):
    monkeypatch.setattr(pricing, "PRICING_PATH", str(tmp_path / "pricing.json"))
    return pricing


def test_reprice_tokens_exact_and_ratio(prices):
    p = prices.load_prices()
    # có token → tính thẳng theo giá model mới
    usd, exact = prices.reprice({"model": "gpt-4.1", "tokens_in": 1_000_000, "tokens_out": 100_000, "usd": 2.8},
                                "gpt-4o-mini", p)
    assert exact and usd == pytest.approx(0.15 + 0.06)
    # chỉ có tiền, cùng tỉ lệ out/in (×4) → quy đổi chính xác
    usd, exact = prices.reprice({"model": "gpt-4.1", "usd": 2.0}, "gpt-4o-mini", p)
    assert exact and usd == pytest.approx(0.15)
    # batch Anthropic -50%
    assert prices.cost("claude-sonnet-5-5", 1_000_000, 0, batch=True) == pytest.approx(1.0)


def test_reprice_ratio_differs_is_approx(prices):
    prices.save_prices({"odd": {"provider": "openai", "in": 1.0, "out": 1.0}})
    usd, exact = prices.reprice({"model": "gpt-4.1", "usd": 2.0}, "odd", prices.load_prices())
    assert not exact and usd == pytest.approx(1.0)


def test_what_if_same_provider_only(prices):
    rows = prices.what_if([{"role": "agent", "model": "gpt-4.1", "usd": 1.0}])
    models = {r["model"] for r in rows}
    assert "gpt-4o-mini" in models and "claude-opus-5-5" not in models and "text-embedding-3-small" not in models
    assert next(r for r in rows if r["model"] == "gpt-4.1")["current"]


def test_save_prices_validates_and_deletes(prices):
    with pytest.raises(ValueError):
        prices.save_prices({"x": {"in": "abc", "out": 1}})
    with pytest.raises(ValueError):
        prices.save_prices({"x": {"in": -1, "out": 1}})
    p = prices.save_prices({"gpt-4o": None, "new-model": {"provider": "openai", "in": 1, "out": 4}})
    assert "gpt-4o" not in p and p["new-model"]["in"] == 1.0


def test_cost_blocks_in_adapters():
    tool = norm_tool_calls({"model": "gpt-4.1", "summary": {"cost_usd": 0.4, "cases": 2, "tokens_in": 100, "tokens_out": 10},
                            "rows": [], "traces": []})
    assert tool["cost"]["source"] == "tokens" and tool["metrics"]["cost.per_case_usd"] == pytest.approx(0.2)
    old = norm_tool_calls({"model": "gpt-4.1", "summary": {"cost_usd": 0.4, "cases": 2}, "rows": [], "traces": []})
    assert old["cost"]["source"] == "cost_only" and old["cost"]["items"][0]["tokens_in"] is None
    mem = norm_memory({"summary": {"cases": 2}, "rows": [], "model": "gpt-4o-mini",
                       "usage": {"gpt-4o-mini": {"role": "memory", "in": 1_000_000, "out": 0},
                                 "text-embedding-3-small": {"role": "embedding", "in": 1_000_000, "out": 0}}})
    assert mem["cost"]["total_usd"] == pytest.approx(0.15 + 0.02) and mem["variant"] == "gpt-4o-mini"
    ret = norm_retrieval({"config": {"skip_judge": True}, "known_item": {}})
    assert ret["cost"]["source"] == "free" and ret["cost"]["total_usd"] == 0.0


def test_estimate_prefers_same_model_and_scales(dirs, prices):
    res, _ = dirs
    def put(name, model, usd):
        (res / name).write_text(json.dumps({"model": model, "summary": {"cost_usd": usd, "cases": 23}, "rows": [],
                                            "traces": []}), encoding="utf-8")
    put("tool_eval_gpt-4.1_20260101_000000.json", "gpt-4.1", 1.6)
    put("tool_eval_gpt-4o-mini_20260102_000000.json", "gpt-4o-mini", 0.09)
    e = estimate_mod.estimate("tool_calls", {"model": "gpt-4.1", "repeat": 2})
    assert e["same_model"] and e["usd"] == pytest.approx(3.2) and "repeat 1 → 2" in e["note"]
    e = estimate_mod.estimate("tool_calls", {"model": "gpt-4.1-mini"})       # chưa chạy model này → quy đổi
    assert not e["same_model"] and e["usd"] == pytest.approx(0.09 * 0.40 / 0.15)
    assert estimate_mod.estimate("unit_tests", {})["usd"] == 0.0
    assert estimate_mod.estimate("memory", {})["usd"] is None                 # chưa có mốc
