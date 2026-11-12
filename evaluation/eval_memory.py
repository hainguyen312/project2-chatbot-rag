"""
Eval bộ nhớ dài hạn (services/memory_manager.py) với LLM + Milvus + MongoDB THẬT.

Mỗi case = chuỗi lượt chat của một người dùng giả (user id riêng "eval-mem-…", tự xoá
sau khi chạy) + kỳ vọng:
    contains / contains_any   memory còn hiệu lực phải nhắc tới các cụm này
    not_contains              không được lưu (dữ liệu nhạy cảm)
    types                     cụm X phải nằm trong memory loại Y (core/episodic/…)
    no_type                   không được sinh memory thuộc các loại này
    max_total                 tổng số memory tối đa sau các lượt
    max_matching              số memory khớp regex tối đa (bắt bản trùng / chưa xoá)
    queries                   truy vấn → retrieve_memories phải trả về fact chứa must_include

Chỉ số: tỉ lệ case đạt, tỉ lệ đạt theo từng loại kiểm tra, số lần lộ dữ liệu nhạy cảm,
số bản trùng, phân bố thao tác ADD/UPDATE/DELETE/NOOP, thời gian.

Chạy (cần OPENAI_API_KEY còn credit, Milvus + MongoDB đang chạy):
    python evaluation/eval_memory.py
    python evaluation/eval_memory.py --only cap_nhat --repeat 3
    python evaluation/eval_memory.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from collections import Counter, defaultdict
from statistics import mean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "evaluation")
sys.path.insert(0, ROOT)

EXPECT_KEYS = {"contains", "contains_any", "not_contains", "types", "no_type", "max_total", "max_matching"}
TYPES = {"core", "episodic", "semantic", "procedural"}


def load_cases(path: str) -> list[dict]:
    cases = json.load(open(path, encoding="utf-8"))
    errors, ids = [], set()
    for c in cases:
        cid = c.get("id", "?")
        if not c.get("id") or not c.get("category") or not c.get("turns"):
            errors.append(f"{cid}: thiếu id/category/turns")
        if cid in ids:
            errors.append(f"{cid}: trùng id")
        ids.add(cid)
        exp = c.get("expect", {})
        if set(exp) - EXPECT_KEYS:
            errors.append(f"{cid}: khoá expect lạ {sorted(set(exp) - EXPECT_KEYS)}")
        bad_types = (set(exp.get("types", {}).values()) | set(exp.get("no_type", []))) - TYPES
        if bad_types:
            errors.append(f"{cid}: loại memory không tồn tại {sorted(bad_types)}")
        for m in exp.get("max_matching", []):
            try:
                re.compile(m["pattern"])
            except Exception as e:
                errors.append(f"{cid}: regex lỗi {m}: {e}")
        if not exp and not c.get("queries"):
            errors.append(f"{cid}: không có kỳ vọng nào")
    if errors:
        raise ValueError("File case lỗi:\n  " + "\n  ".join(errors))
    return cases


def _has(memories: list[dict], phrase: str) -> list[dict]:
    p = phrase.lower()
    return [m for m in memories if p in (m.get("fact") or "").lower()]


def score_case(case: dict, memories: list[dict], retrieved: dict[str, list[dict]]) -> dict:
    """memories = memory còn hiệu lực sau mọi lượt; retrieved = {query: kết quả retrieve}."""
    exp = case.get("expect", {})
    checks: dict[str, bool] = {}
    for ph in exp.get("contains", []):
        checks[f"contains:{ph}"] = bool(_has(memories, ph))
    for group in exp.get("contains_any", []):
        checks[f"contains_any:{'|'.join(group)}"] = any(_has(memories, ph) for ph in group)
    for ph in exp.get("not_contains", []):
        checks[f"not_contains:{ph}"] = not _has(memories, ph)
    for ph, t in exp.get("types", {}).items():
        checks[f"type:{ph}={t}"] = any((m.get("mem_type") or m.get("type")) == t for m in _has(memories, ph))
    if exp.get("no_type"):
        checks[f"no_type:{','.join(exp['no_type'])}"] = not any(
            (m.get("mem_type") or m.get("type")) in exp["no_type"] for m in memories)
    if "max_total" in exp:
        checks[f"max_total:{exp['max_total']}"] = len(memories) <= exp["max_total"]
    for mm in exp.get("max_matching", []):
        n = sum(bool(re.search(mm["pattern"], m.get("fact") or "", re.I)) for m in memories)
        checks[f"max_matching:{mm['pattern']}<={mm['max']}"] = n <= mm["max"]
    for q in case.get("queries", []):
        got = retrieved.get(q["q"], [])
        for ph in q.get("must_include", []):
            checks[f"retrieve:{q['q'][:30]}→{ph}"] = bool(_has(got, ph))
    return {"id": case["id"], "category": case["category"], "pass": all(checks.values()) if checks else False,
            "checks": checks, "n_memories": len(memories)}


def run_case(case: dict, mm, user_id: str | None = None) -> dict:
    """Chạy các lượt chat, lấy memory + kết quả truy vấn, chấm điểm, rồi XOÁ dữ liệu eval."""
    uid = user_id or f"eval-mem-{case['id']}-{uuid.uuid4().hex[:8]}"
    actions: list[str] = []
    t0 = time.time()
    try:
        for turn in case["turns"]:
            user_msg, bot_msg = (turn, "Cảm ơn bạn, mình đã ghi nhận.") if isinstance(turn, str) else turn
            out = mm.process_turn(uid, user_msg, bot_msg)
            actions += [a["action"] for a in out.get("actions", [])]
        memories = mm.list_user_memories(uid)
        retrieved = {q["q"]: mm.retrieve_memories(uid, q["q"]) for q in case.get("queries", [])}
        result = score_case(case, memories, retrieved)
        result.update(actions=actions, facts=[m.get("fact") for m in memories], latency_s=time.time() - t0)
        return result
    finally:
        mm.delete_all_for_user(uid)


def summarize(rows: list[dict]) -> dict:
    by_kind: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        for k, ok in r["checks"].items():
            by_kind[k.split(":")[0]].append(ok)
    acts = Counter(a for r in rows for a in r.get("actions", []))
    return {
        "cases": len(rows),
        "case_pass": mean(float(r["pass"]) for r in rows) if rows else None,
        "by_check": {k: round(mean(map(float, v)), 3) for k, v in sorted(by_kind.items())},
        "sensitive_leaks": sum(not ok for r in rows for k, ok in r["checks"].items() if k.startswith("not_contains")),
        "duplicate_or_stale": sum(not ok for r in rows for k, ok in r["checks"].items() if k.startswith("max_matching")),
        "actions": dict(acts),
        "avg_latency_s": round(mean(r.get("latency_s") or 0 for r in rows), 2) if rows else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(BASE, "memory_cases.json"))
    ap.add_argument("--only", default=None, help="chỉ chạy category này")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cases = load_cases(args.cases)
    if args.only:
        cases = [c for c in cases if c["category"] == args.only]
    print(f"[eval-memory] {len(cases)} case · {len({c['category'] for c in cases})} nhóm")
    if args.dry_run:
        return

    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    from openai import OpenAI
    from services.memory_manager import MemoryManager

    mm = MemoryManager(openai_client=OpenAI(), mongo_uri=os.getenv("MONGODB_URI", ""))
    if not mm._ready():
        raise SystemExit("MemoryManager chưa sẵn sàng (Milvus/MongoDB)")

    rows = []
    for _ in range(args.repeat):
        for c in cases:
            r = run_case(c, mm)
            rows.append(r)
            bad = [k for k, ok in r["checks"].items() if not ok]
            print(f"  {'✓' if r['pass'] else '✗'} {c['id']:<28} {r['actions']} {('→ trượt ' + str(bad)) if bad else ''}")
    s = summarize(rows)
    print("\n" + json.dumps(s, ensure_ascii=False, indent=2))
    path = os.path.join(BASE, "results", f"memory_eval_{time.strftime('%Y%m%d_%H%M%S')}.json")
    json.dump({"summary": s, "rows": rows}, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✓ Lưu: {path}")


if __name__ == "__main__":
    main()
