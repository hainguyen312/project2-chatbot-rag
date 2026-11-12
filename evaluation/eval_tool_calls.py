"""
Eval hành vi gọi tool của agent ReAct (services/agentic_rag.run_agentic_rag_sync).

Không chấm chất lượng câu trả lời pháp lý (đã có eval_retrieval_nogt.py cho retrieval);
ở đây đo agent có GỌI ĐÚNG TOOL, ĐÚNG THAM SỐ, ĐÚNG LÚC hay không, trên bộ case có kỳ vọng
ghi sẵn (evaluation/tool_call_cases.json).

Mỗi case có thể khai báo:
    must_call          tool bắt buộc phải gọi (vd. web_search cho câu hỏi mức phạt)
    should_call        tool nên gọi (đo riêng, không tính trượt case)
    must_not_call      tool không được gọi (vd. web_search cho câu định nghĩa ổn định)
    first_tool         tool phải gọi đầu tiên
    expect_forced_web  true/false: luật tự kiểm tra web có (không) được kích hoạt
    max_tool_calls     trần số lần gọi tool
    answer_must_contain / answer_must_contain_any   kiểm tra nhanh câu trả lời

Chỉ số tổng hợp (theo model, theo nhóm câu hỏi):
    case_pass            tỉ lệ case đạt MỌI kỳ vọng cứng
    must_call_recall     tỉ lệ tool bắt buộc được gọi
    should_call_recall   tỉ lệ tool "nên gọi" được gọi
    forbidden_rate       tỉ lệ case gọi tool bị cấm
    first_tool_acc       tool đầu tiên đúng kỳ vọng
    forced_web_acc       luật buộc web kích hoạt đúng kỳ vọng
    invalid_id_rate      tỉ lệ lượt graph_traverse/get_full_article truyền mapc CHƯA từng xuất hiện
                         trong kết quả tool trước đó (= agent bịa mã điều luật)
    empty_arg_rate       tỉ lệ lượt gọi thiếu tham số bắt buộc
    redundant_rate       tỉ lệ lượt gọi trùng (cùng tool + cùng tham số) trong một case
    over_budget_rate     tỉ lệ case vượt max_tool_calls
    max_iter_rate        tỉ lệ case chạm trần vòng lặp
    answer_check         tỉ lệ case câu trả lời có đủ từ khoá kỳ vọng
    avg_calls / avg_latency_s / cost_usd

Chạy (cần OPENAI_API_KEY còn credit, Milvus + MySQL đang chạy):
    python evaluation/eval_tool_calls.py --model gpt-4.1
    python evaluation/eval_tool_calls.py --model gpt-4o-mini --repeat 2
    python evaluation/eval_tool_calls.py --dry-run          # chỉ kiểm tra file case
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from statistics import mean
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "evaluation")
sys.path.insert(0, ROOT)

TOOLS = {"vector_search", "graph_traverse", "web_search", "get_full_article"}
REQUIRED_ARG = {"vector_search": "query", "web_search": "query", "graph_traverse": "mapcs",
                "get_full_article": "mapc"}
CASE_KEYS = {"id", "category", "query", "must_call", "should_call", "must_not_call", "first_tool",
             "expect_forced_web", "max_tool_calls", "answer_must_contain", "answer_must_contain_any", "note"}
# $/1M token (input, output) — ước tính chi phí
PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.0, 8.0), "gpt-4o": (2.5, 10.0)}


# ── Dataset ──────────────────────────────────────────────────────────────────
def load_cases(path: str) -> list[dict]:
    cases = json.load(open(path, encoding="utf-8"))
    errors, ids = [], set()
    for i, c in enumerate(cases):
        cid = c.get("id", f"#{i}")
        if not c.get("id") or not c.get("query") or not c.get("category"):
            errors.append(f"{cid}: thiếu id/query/category")
        if cid in ids:
            errors.append(f"{cid}: trùng id")
        ids.add(cid)
        if set(c) - CASE_KEYS:
            errors.append(f"{cid}: khoá lạ {sorted(set(c) - CASE_KEYS)}")
        for k in ("must_call", "should_call", "must_not_call"):
            bad = set(c.get(k, [])) - TOOLS
            if bad:
                errors.append(f"{cid}: {k} có tool không tồn tại {sorted(bad)}")
        if c.get("first_tool") and c["first_tool"] not in TOOLS:
            errors.append(f"{cid}: first_tool không tồn tại")
        if set(c.get("must_call", [])) & set(c.get("must_not_call", [])):
            errors.append(f"{cid}: một tool vừa must_call vừa must_not_call")
    if errors:
        raise ValueError("File case lỗi:\n  " + "\n  ".join(errors))
    return cases


# ── Chấm một case (hàm thuần, unit test được) ────────────────────────────────
def _ids_in(content: str) -> set[str]:
    """mapc xuất hiện trong kết quả tool (JSON) — để kiểm agent có bịa mã không."""
    try:
        data = json.loads(content)
    except Exception:
        return set()
    rows = data if isinstance(data, list) else [data]
    # Tool chấp nhận cả mapc lẫn ma_phap_dien (resolve_article_ids) → cả hai đều là id hợp lệ
    return {str(r[k]) for r in rows if isinstance(r, dict) for k in ("mapc", "ma_phap_dien") if r.get(k)}


def score_case(case: dict, trace: dict) -> dict:
    """
    trace = {"calls": [{"name", "args", "result"}...], "forced_web": bool, "answer": str,
             "iterations": int, "max_iterations": int}
    """
    calls = trace["calls"]
    names = [c["name"] for c in calls]
    called = set(names)
    must, should, banned = set(case.get("must_call", [])), set(case.get("should_call", [])), set(case.get("must_not_call", []))

    seen_ids: set[str] = set()
    invalid_id_calls = id_calls = empty_args = redundant = 0
    signatures = set()
    for c in calls:
        args = c.get("args") or {}
        sig = (c["name"], json.dumps(args, sort_keys=True, ensure_ascii=False))
        redundant += sig in signatures
        signatures.add(sig)
        req = REQUIRED_ARG.get(c["name"])
        val = args.get(req) if req else None
        if req and (val is None or (isinstance(val, (str, list)) and not val)
                    or (isinstance(val, str) and not val.strip())):
            empty_args += 1
        if c["name"] in ("graph_traverse", "get_full_article"):
            ids = args.get("mapcs") if c["name"] == "graph_traverse" else [args.get("mapc")]
            ids = [str(x) for x in (ids or []) if x]
            if ids:
                id_calls += 1
                invalid_id_calls += any(x not in seen_ids for x in ids)
        seen_ids |= _ids_in(c.get("result") or "")

    answer = (trace.get("answer") or "").lower()
    checks = {
        "must_call": must <= called,
        "must_not_call": not (banned & called),
        "first_tool": (not case.get("first_tool")) or (bool(names) and names[0] == case["first_tool"]),
        "forced_web": case.get("expect_forced_web") is None or trace.get("forced_web") == case["expect_forced_web"],
        "budget": case.get("max_tool_calls") is None or len(calls) <= case["max_tool_calls"],
        "answer": (all(k.lower() in answer for k in case.get("answer_must_contain", []))
                   and (not case.get("answer_must_contain_any")
                        or any(k.lower() in answer for k in case["answer_must_contain_any"]))),
    }
    return {
        "id": case["id"], "category": case["category"],
        "pass": all(checks.values()),
        "checks": checks,
        "tools": names,
        "must_recall": len(must & called) / len(must) if must else None,
        "should_recall": len(should & called) / len(should) if should else None,
        "forbidden": bool(banned & called),
        "forced_web": bool(trace.get("forced_web")),
        "n_calls": len(calls),
        "id_calls": id_calls, "invalid_id_calls": invalid_id_calls,
        "empty_args": empty_args, "redundant": redundant,
        "hit_max_iter": trace.get("iterations", 0) >= trace.get("max_iterations", 99),
        "latency_s": trace.get("latency_s"), "cost_usd": trace.get("cost_usd"),
    }


def _ratio(xs):
    xs = [x for x in xs if x is not None]
    return mean(xs) if xs else None


def aggregate(rows: list[dict]) -> dict:
    n_calls = sum(r["n_calls"] for r in rows)
    id_calls = sum(r["id_calls"] for r in rows)
    return {
        "cases": len(rows),
        "case_pass": _ratio([float(r["pass"]) for r in rows]),
        "must_call_recall": _ratio([r["must_recall"] for r in rows]),
        "should_call_recall": _ratio([r["should_recall"] for r in rows]),
        "forbidden_rate": _ratio([float(r["forbidden"]) for r in rows]),
        "first_tool_acc": _ratio([float(r["checks"]["first_tool"]) for r in rows]),
        "forced_web_acc": _ratio([float(r["checks"]["forced_web"]) for r in rows]),
        "invalid_id_rate": (sum(r["invalid_id_calls"] for r in rows) / id_calls) if id_calls else None,
        "empty_arg_rate": (sum(r["empty_args"] for r in rows) / n_calls) if n_calls else None,
        "redundant_rate": (sum(r["redundant"] for r in rows) / n_calls) if n_calls else None,
        "over_budget_rate": _ratio([float(not r["checks"]["budget"]) for r in rows]),
        "max_iter_rate": _ratio([float(r["hit_max_iter"]) for r in rows]),
        "answer_check": _ratio([float(r["checks"]["answer"]) for r in rows]),
        "avg_calls": n_calls / len(rows) if rows else None,
        "avg_latency_s": _ratio([r["latency_s"] for r in rows]),
        "cost_usd": sum(r["cost_usd"] or 0 for r in rows),
    }


# ── Chạy agent thật, ghi trace ───────────────────────────────────────────────
class _UsageClient:
    """Bọc OpenAI client: cộng dồn token để ước tính chi phí."""

    def __init__(self, client):
        self._c, self.prompt_tokens, self.completion_tokens = client, 0, 0
        from types import SimpleNamespace
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        r = self._c.chat.completions.create(**kw)
        u = getattr(r, "usage", None)
        if u:
            self.prompt_tokens += u.prompt_tokens or 0
            self.completion_tokens += u.completion_tokens or 0
        return r


def run_case(case: dict, *, agent_module, openai_client, retriever, neo4j_driver, max_iterations: int,
             model: str) -> dict:
    trace = {"calls": [], "forced_web": False, "max_iterations": max_iterations}
    orig_exec, orig_force = agent_module.execute_agent_tool, agent_module.web_verify_instruction

    def exec_spy(name, args, **kw):
        content, hits = orig_exec(name, args, **kw)
        trace["calls"].append({"name": name, "args": args, "result": content, "n_hits": len(hits or [])})
        return content, hits

    def force_spy(reason):
        trace["forced_web"] = True
        trace["forced_reason"] = reason
        return orig_force(reason)

    agent_module.execute_agent_tool, agent_module.web_verify_instruction = exec_spy, force_spy
    usage = _UsageClient(openai_client)
    t0 = time.time()
    try:
        out = agent_module.run_agentic_rag_sync(
            openai_client=usage, retriever=retriever, neo4j_driver=neo4j_driver,
            user_prompt=case["query"], history=[], rerank_query=case["query"],
            max_iterations=max_iterations,
        )
        trace["answer"], trace["iterations"] = out["answer"], out["iterations"]
    except Exception as e:
        trace["answer"], trace["iterations"], trace["error"] = "", 0, str(e)
    finally:
        agent_module.execute_agent_tool, agent_module.web_verify_instruction = orig_exec, orig_force
    trace["latency_s"] = time.time() - t0
    pin, pout = PRICES.get(model, (0.0, 0.0))
    trace["cost_usd"] = (usage.prompt_tokens * pin + usage.completion_tokens * pout) / 1e6
    return trace


def print_report(model: str, rows: list[dict]):
    overall = aggregate(rows)
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    fmt = lambda v: "-" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))  # noqa: E731
    print(f"\n=== {model} · {overall['cases']} lượt ===")
    for k, v in overall.items():
        print(f"  {k:<20} {fmt(v)}")
    print("\n  Theo nhóm: pass / must_recall / gọi TB")
    for cat, rs in sorted(by_cat.items()):
        a = aggregate(rs)
        print(f"  {cat:<20} {fmt(a['case_pass'])} / {fmt(a['must_call_recall'])} / {fmt(a['avg_calls'])}")
    fails = [r for r in rows if not r["pass"]]
    if fails:
        print("\n  Case trượt (kiểm tra sai → chuỗi tool đã gọi):")
        for r in fails:
            bad = [k for k, ok in r["checks"].items() if not ok]
            print(f"   - {r['id']}: {bad} → {r['tools']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.getenv("OPENAI_AGENT_MODEL", "gpt-4o-mini"))
    ap.add_argument("--cases", default=os.path.join(BASE, "tool_call_cases.json"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", default=None, help="chỉ chạy các case thuộc category này")
    ap.add_argument("--ids", default=None, help="chỉ chạy các case có id này (phân cách bằng dấu phẩy)")
    ap.add_argument("--repeat", type=int, default=1, help="chạy mỗi case N lần (agent không tất định)")
    ap.add_argument("--max-iterations", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true", help="chỉ kiểm tra file case")
    args = ap.parse_args()

    cases = load_cases(args.cases)
    if args.only:
        cases = [c for c in cases if c["category"] == args.only]
    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    cases = cases[: args.limit] if args.limit else cases
    print(f"[eval] {len(cases)} case · {len({c['category'] for c in cases})} nhóm · model={args.model}")
    if args.dry_run:
        return

    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    os.environ["OPENAI_AGENT_MODEL"] = args.model           # agent_chat_model() đọc lúc chạy
    from openai import OpenAI
    from pymilvus import Collection, connections

    import services.agentic_rag as A
    from retrieve.build_graph import GraphRAGRetriever, get_neo4j
    from retrieve.lexical_bm25 import get_bm25

    connections.connect("default", host=os.getenv("MILVUS_HOST", "localhost"), port=os.getenv("MILVUS_PORT", "19530"))
    col = Collection("phapdien_simple_tendieu")
    col.load()
    client = OpenAI()
    driver = get_neo4j()
    retriever = GraphRAGRetriever(driver, col, client)
    get_bm25()

    rows, traces = [], []
    for rep in range(args.repeat):
        for c in cases:
            tr = run_case(c, agent_module=A, openai_client=client, retriever=retriever, neo4j_driver=driver,
                          max_iterations=args.max_iterations, model=args.model)
            if tr.get("error"):
                print(f"  ⚠ {c['id']}: {tr['error'][:160]}")
                if "insufficient_quota" in tr["error"] or "no credits" in tr["error"]:
                    raise SystemExit("OpenAI hết credit — dừng eval.")
            r = score_case(c, tr)
            rows.append(r)
            traces.append({"case": c["id"], "rep": rep, **{k: v for k, v in tr.items() if k != "calls"},
                           "calls": [{k: v for k, v in x.items() if k != "result"} for x in tr["calls"]]})
            print(f"  {'✓' if r['pass'] else '✗'} {c['id']:<22} {r['tools']}{' [buộc web]' if r['forced_web'] else ''}")

    print_report(args.model, rows)
    path = os.path.join(BASE, "results", f"tool_eval_{args.model}_{time.strftime('%Y%m%d_%H%M%S')}.json")
    json.dump({"model": args.model, "summary": aggregate(rows), "rows": rows, "traces": traces},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n✓ Lưu: {path}")


if __name__ == "__main__":
    main()
