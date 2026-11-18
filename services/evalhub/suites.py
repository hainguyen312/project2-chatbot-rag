"""
Registry các bộ đánh giá (suite) mà trang admin hiển thị và chạy được.

Mỗi suite khai báo:
  - cách chạy (argv, tham số cho phép chỉnh trên UI),
  - file kết quả của nó trong evaluation/results/ (theo tiền tố tên file),
  - danh sách chỉ số (nhãn, định dạng, chiều tốt hơn, chỉ số chính),
  - adapter `normalize(raw)` chuyển file kết quả riêng của script → định dạng chung:

    {
      "metrics": {key: number},           # phẳng; key khớp MetricSpec.key (key lạ vẫn hiển thị)
      "variant": str | None,              # chiều so sánh phụ, vd. model của tool-call eval
      "config":  dict,                    # tham số lúc chạy
      "tables":  [{"title", "columns": [..], "rows": [{"label", "values": [..]}]}],
      "groups":  [{"id", "title", "columns": [{"key","label","fmt"}],
                   "rows": [{"id","label","category","pass","cells":{..},"detail":{..}}]}],
    }

Thêm một bộ đánh giá mới = viết script ghi JSON (nhận `--out`) + thêm một Suite vào SUITES.
Frontend không cần sửa.
"""
from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVAL_DIR = os.path.join(ROOT, "evaluation")
# Nơi GHI (lần chạy mới, log, bảng giá). Mặc định = evaluation/; image production đặt /data/eval
# (volume) vì mã nguồn trong image chỉ đọc. Kết quả cũ trong evaluation/results/ vẫn được đọc.
DATA_DIR = os.getenv("EVAL_DATA_DIR") or EVAL_DIR


def runs_enabled() -> bool:
    """EVAL_RUNS_ENABLED=0 tắt hẳn việc chạy từ UI (vd. production: không tiêu key thật)."""
    return os.getenv("EVAL_RUNS_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


@dataclass
class Param:
    name: str                      # tên tham số (cũng là khoá trong body POST)
    label: str
    type: str                      # int | float | bool | str | select
    default: Any = None
    flag: str = ""                 # cờ CLI; mặc định --<name với dấu ->
    help: str = ""
    options: list[str] = field(default_factory=list)
    min: Optional[float] = None
    max: Optional[float] = None

    def cli_flag(self) -> str:
        return self.flag or "--" + self.name.replace("_", "-")


@dataclass
class MetricSpec:
    key: str
    label: str
    fmt: str = "ratio"             # pct | ratio | int | num | sec | usd
    better: str = "up"             # up | down | none
    headline: bool = False
    group: str = ""


@dataclass
class Suite:
    id: str
    name: str
    description: str
    kind: str                      # eval | benchmark | test
    file_prefix: str               # file kết quả: evaluation/results/<file_prefix>*.json
    script: list[str]              # argv sau python, vd. ["evaluation/eval_memory.py"]
    params: list[Param]
    metrics: list[MetricSpec]
    normalize: Callable[[dict], dict]
    cost_hint: str = ""
    requires: list[str] = field(default_factory=list)
    out_ext: str = ".json"
    # Script không tự ghi JSON (vd. pytest → junit XML): file tạm → JSON chung.
    postprocess: Optional[Callable[[str, str], None]] = None
    variant_label: str = ""
    # Tham số tạo "biến thể" (vd. model, bộ lọc test): chỉ so xu hướng / chênh lệch giữa các lần cùng biến thể.
    variant_param: str = ""
    # Tham số chọn model → vai trò trong chi phí (vd. {"model": "agent"}); dùng để ước tính trước khi chạy.
    model_params: dict[str, str] = field(default_factory=dict)
    # Tham số nhân số lượt gọi API (vd. repeat, n) — ước tính tỉ lệ thuận so với lần chạy làm mốc.
    scale_params: list[str] = field(default_factory=list)

    def build_argv(self, params: dict, out_path: str) -> list[str]:
        argv = [sys.executable, *self.script]
        for p in self.params:
            v = params.get(p.name, p.default)
            if p.type == "bool":
                if v:
                    argv.append(p.cli_flag())
            elif v is not None and v != "":
                argv += [p.cli_flag(), str(v)]
        target = out_path + ".raw" if self.postprocess else out_path
        argv += self.out_args(target)
        return argv

    def out_args(self, path: str) -> list[str]:
        return ["--out", path]

    def availability(self) -> tuple[bool, str]:
        """Chạy được trên máy này không (script có trong image / bật cờ)."""
        if not runs_enabled():
            return False, "Chạy từ trang admin đang tắt (EVAL_RUNS_ENABLED=0)."
        target = next((a for a in self.script if a.endswith(".py") or a == "tests"), "")
        if target and not os.path.exists(os.path.join(ROOT, target)):
            return False, (f"Máy chủ này không có {target} (image production không chứa script eval). "
                           "Chạy trên máy dev — kết quả commit vào evaluation/results/ sẽ hiện ở đây.")
        return True, ""

    def public(self) -> dict:
        d = asdict(self)
        for k in ("normalize", "postprocess", "script", "out_ext"):
            d.pop(k, None)
        d["runnable"], d["run_disabled_reason"] = self.availability()
        return d


# ------------------------------------------------------------------ chuẩn hoá tham số
def coerce_params(suite: Suite, raw: dict) -> dict:
    """Kiểm tra + ép kiểu tham số từ UI theo khai báo; tham số lạ bị từ chối."""
    raw = raw or {}
    unknown = set(raw) - {p.name for p in suite.params}
    if unknown:
        raise ValueError(f"Tham số không hợp lệ: {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for p in suite.params:
        v = raw.get(p.name, p.default)
        if v is None or v == "":
            out[p.name] = None if p.type != "bool" else False
            continue
        try:
            if p.type == "int":
                v = int(v)
            elif p.type == "float":
                v = float(v)
            elif p.type == "bool":
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            else:
                v = str(v).strip()
        except (TypeError, ValueError):
            raise ValueError(f"{p.label}: giá trị không hợp lệ")
        if p.type == "select" and v not in p.options:
            raise ValueError(f"{p.label}: phải là một trong {', '.join(p.options)}")
        if p.type == "str" and (len(v) > 200 or v.startswith("-")):
            raise ValueError(f"{p.label}: giá trị không hợp lệ")
        if p.type in ("int", "float"):
            if p.min is not None and v < p.min:
                raise ValueError(f"{p.label}: tối thiểu {p.min}")
            if p.max is not None and v > p.max:
                raise ValueError(f"{p.label}: tối đa {p.max}")
        out[p.name] = v
    return out


# ------------------------------------------------------------------ tiện ích adapter
def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _flatten(d: dict, prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if _num(v):
            out[key] = v
        elif isinstance(v, dict):
            out.update(_flatten(v, key + "."))
    return out


def _matrix(title: str, data: dict[str, dict]) -> dict:
    """{pipeline: {metric: v}} → bảng pipeline × chỉ số."""
    cols: list[str] = []
    for row in data.values():
        for c in row:
            if c not in cols:
                cols.append(c)
    return {"title": title, "columns": cols,
            "rows": [{"label": n, "values": [row.get(c) for c in cols]} for n, row in data.items()]}


def _scalar_cols(rows: list[dict], skip: set[str]) -> list[dict]:
    cols: list[str] = []
    for r in rows:
        for k, v in r.items():
            if k not in skip and k not in cols and (v is None or isinstance(v, (str, int, float, bool))):
                cols.append(k)
    return [{"key": c, "label": c, "fmt": "auto"} for c in cols]


def _cost_block(items: list[dict], n_cases: Optional[int], source: str, note: str = "") -> dict:
    """Khoản chi phí chung: items = [{role, model, tokens_in, tokens_out, usd, batch}].

    source: tokens (có số token) | cost_only (chỉ có số tiền) | free (không gọi API) | none (không ghi nhận).
    """
    total = sum(i["usd"] for i in items if i.get("usd") is not None) if items else (0.0 if source == "free" else None)
    tin = [i.get("tokens_in") for i in items]
    tout = [i.get("tokens_out") for i in items]
    return {
        "total_usd": total,
        "per_case_usd": (total / n_cases) if (total is not None and n_cases) else None,
        "tokens_in": sum(tin) if items and all(t is not None for t in tin) else None,
        "tokens_out": sum(tout) if items and all(t is not None for t in tout) else None,
        "n_cases": n_cases, "items": items, "source": source, "note": note,
    }


def _cost_metrics(c: dict) -> dict[str, float]:
    return {f"cost.{k}": c[k] for k in ("total_usd", "per_case_usd", "tokens_in", "tokens_out") if _num(c.get(k))}


# ------------------------------------------------------------------ adapter: tool-call
def norm_tool_calls(raw: dict) -> dict:
    traces = {}
    for t in raw.get("traces") or []:
        traces.setdefault((t.get("case"), t.get("rep", 0)), t)
    seen: dict[str, int] = {}
    rows = []
    for r in raw.get("rows") or []:
        rep = seen.get(r["id"], 0)
        seen[r["id"]] = rep + 1
        rid = r["id"] if rep == 0 else f"{r['id']}#{rep + 1}"
        tr = traces.get((r["id"], rep)) or {}
        rows.append({
            "id": rid, "label": r["id"], "category": r.get("category"), "pass": r.get("pass"),
            "cells": {"tools": " → ".join(r.get("tools") or []) or "—", "n_calls": r.get("n_calls"),
                      "forced_web": r.get("forced_web"), "latency_s": r.get("latency_s"),
                      "cost_usd": r.get("cost_usd")},
            "detail": {"checks": r.get("checks"), "answer": tr.get("answer"), "calls": tr.get("calls"),
                       "error": tr.get("error"),
                       **{k: v for k, v in r.items() if k not in ("checks", "tools", "id", "category", "pass")}},
        })
    s = raw.get("summary") or {}
    model = raw.get("model")
    has_tok = _num(s.get("tokens_in")) and _num(s.get("tokens_out"))
    cost = _cost_block(
        [{"role": "agent", "model": model, "usd": s.get("cost_usd"),
          "tokens_in": s.get("tokens_in") if has_tok else None, "tokens_out": s.get("tokens_out") if has_tok else None,
          "batch": False}] if _num(s.get("cost_usd")) else [],
        s.get("cases") or len(rows), "tokens" if has_tok else ("cost_only" if _num(s.get("cost_usd")) else "none"),
        "" if has_tok else "Lần chạy cũ chỉ ghi số tiền (không có số token).")
    return {
        "cost": cost,
        "metrics": {**_flatten(s), **_cost_metrics(cost)},
        "variant": raw.get("model"),
        "config": {"model": raw.get("model")},
        "tables": [],
        "groups": [{"id": "cases", "title": "Từng case", "columns": [
            {"key": "tools", "label": "Tool đã gọi", "fmt": "text"},
            {"key": "n_calls", "label": "Số lượt", "fmt": "int"},
            {"key": "forced_web", "label": "Buộc web", "fmt": "bool"},
            {"key": "latency_s", "label": "Thời gian", "fmt": "sec"},
            {"key": "cost_usd", "label": "Chi phí", "fmt": "usd"},
        ], "rows": rows}],
    }


# ------------------------------------------------------------------ adapter: memory
def norm_memory(raw: dict) -> dict:
    s = raw.get("summary") or {}
    metrics = _flatten({k: v for k, v in s.items() if k != "actions"})
    metrics.update(_flatten(s.get("actions") or {}, "actions."))
    seen: dict[str, int] = {}
    rows = []
    for r in raw.get("rows") or []:
        n = seen.get(r["id"], 0)
        seen[r["id"]] = n + 1
        failed = [k for k, ok in (r.get("checks") or {}).items() if not ok]
        rows.append({
            "id": r["id"] if n == 0 else f"{r['id']}#{n + 1}", "label": r["id"],
            "category": r.get("category"), "pass": r.get("pass"),
            "cells": {"actions": ", ".join(r.get("actions") or []) or "—", "n_memories": r.get("n_memories"),
                      "failed": ", ".join(failed) or "—", "latency_s": r.get("latency_s")},
            "detail": {"checks": r.get("checks"), "facts": r.get("facts")},
        })
    usage = raw.get("usage") or {}
    from .pricing import cost as _price  # import trễ: pricing import suites

    items = [{"role": u.get("role") or "memory", "model": m, "tokens_in": u.get("in"), "tokens_out": u.get("out", 0),
              "usd": u["usd"] if _num(u.get("usd")) else _price(m, u.get("in") or 0, u.get("out") or 0),
              "batch": False} for m, u in usage.items()]
    cost = _cost_block(items, s.get("cases"), "tokens" if items else "none",
                       "" if items else "Lần chạy cũ không ghi nhận token / chi phí.")
    metrics.update(_cost_metrics(cost))
    return {
        "cost": cost,
        "metrics": metrics, "variant": raw.get("model"),
        "config": {"model": raw.get("model")} if raw.get("model") else {},
        "tables": [{"title": "Tỉ lệ đạt theo loại kiểm tra", "columns": ["Tỉ lệ đạt"],
                    "rows": [{"label": k, "values": [v]} for k, v in (s.get("by_check") or {}).items()]}],
        "groups": [{"id": "cases", "title": "Từng case", "columns": [
            {"key": "actions", "label": "Hành động", "fmt": "text"},
            {"key": "n_memories", "label": "Số ký ức", "fmt": "int"},
            {"key": "failed", "label": "Kiểm tra trượt", "fmt": "text"},
            {"key": "latency_s", "label": "Thời gian", "fmt": "sec"},
        ], "rows": rows}],
    }


# ------------------------------------------------------------------ adapter: retrieval
_SYNTH_CACHE: dict[str, Any] = {}


def _synthetic_queries() -> list[dict]:
    path = os.path.join(EVAL_DIR, "synthetic_queries.json")
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return []
    if _SYNTH_CACHE.get("mtime") != mt:
        try:
            _SYNTH_CACHE.update(mtime=mt, data=json.load(open(path, encoding="utf-8")))
        except (OSError, ValueError):
            return []
    return _SYNTH_CACHE.get("data") or []


def norm_retrieval(raw: dict) -> dict:
    cfg = raw.get("config") or {}
    k = cfg.get("k", 10)
    metrics = {**_flatten(raw.get("summary") or {}, "judge."), **_flatten(raw.get("known_item") or {}, "known_item.")}
    tables, groups = [], []
    if raw.get("summary"):
        tables.append(_matrix(f"Pooled LLM-judge (judge = {cfg.get('judge_model', '?')})", raw["summary"]))
    if raw.get("known_item"):
        tables.append(_matrix(f"Known-item ({cfg.get('synthetic', '?')} câu tổng hợp)", raw["known_item"]))

    pq = raw.get("per_query") or {}
    if pq:
        names = list(pq)
        nd = f"nDCG@{k}"
        queries = list(next(iter(pq.values())).keys())
        groups.append({"id": "judge", "title": f"Câu hỏi thật — {nd} theo pipeline",
                       "columns": [{"key": n, "label": n, "fmt": "ratio"} for n in names],
                       "rows": [{"id": q, "label": q, "category": None, "pass": None,
                                 "cells": {n: (pq[n].get(q) or {}).get(nd) for n in names},
                                 "detail": {n: pq[n].get(q) for n in names}} for q in queries]})
    ranks = raw.get("known_item_ranks") or {}
    if ranks:
        names = list(ranks)
        synth = _synthetic_queries()
        n_rows = max(len(v) for v in ranks.values())
        main = "hybrid_new" if "hybrid_new" in ranks else names[0]
        rows = []
        for i in range(n_rows):
            q = synth[i] if i < len(synth) else {}
            rank = ranks[main][i] if i < len(ranks[main]) else None
            rows.append({"id": f"ki{i + 1}", "label": q.get("query") or f"Câu #{i + 1}",
                         "category": None, "pass": rank is not None and rank <= k,
                         "cells": {n: (ranks[n][i] if i < len(ranks[n]) else None) for n in names},
                         "detail": {"gold": q.get("gold"), "source_title": q.get("source_title")}})
        groups.append({"id": "known_item", "title": f"Known-item — hạng của điều gốc (trống = ngoài top {k}); "
                                                    f"đạt = {main} tìm thấy",
                       "columns": [{"key": n, "label": n, "fmt": "rank"} for n in names], "rows": rows})
    usage = (raw.get("usage") or {}).get("judge")
    n_q = len(next(iter(pq.values()))) if pq else None
    if usage:
        cost = _cost_block([{"role": "judge", "model": usage.get("model"), "tokens_in": usage.get("in"),
                             "tokens_out": usage.get("out"), "usd": usage.get("usd"),
                             "batch": bool(usage.get("batch"))}], n_q, "tokens",
                           "Chỉ tính lượt chấm mới; cặp đã có trong cache chấm không tốn thêm.")
    elif cfg.get("skip_judge"):
        cost = _cost_block([], None, "free", "Bỏ qua LLM-judge — không gọi Claude (chỉ embedding truy vấn, không đáng kể).")
    else:
        cost = _cost_block([], n_q, "none", "Lần chạy cũ không ghi chi phí judge.")
    metrics.update(_cost_metrics(cost))
    return {"cost": cost, "metrics": metrics, "variant": None, "config": cfg, "tables": tables, "groups": groups}


# ------------------------------------------------------------------ adapter: exceptions
def norm_exceptions(raw: dict) -> dict:
    metrics = {}
    tables, groups = [], []
    for sec in ("retrieval", "answers"):
        part = raw.get(sec) or {}
        s = part.get("summary") or {}
        metrics.update(_flatten(s, f"{sec}."))
        if s:
            tables.append({"title": "Truy hồi" if sec == "retrieval" else "Câu trả lời", "columns": ["Giá trị"],
                           "rows": [{"label": k, "values": [v]} for k, v in s.items() if _num(v)]})
        rows = part.get("rows") or []
        if rows:
            label_key = "cau_hoi" if "cau_hoi" in rows[0] else None
            cols = _scalar_cols(rows, {"cau_hoi", "query"})
            groups.append({
                "id": sec, "title": "Truy hồi từng case — A = điều chính, B = ngoại lệ; đạt = có B khi BẬT"
                if sec == "retrieval" else "Câu trả lời từng case",
                "columns": cols,
                "rows": [{"id": f"{sec}{i + 1}", "label": (r.get(label_key) if label_key else None) or f"Case #{i + 1}",
                          "category": None, "pass": r.get("b_on") if sec == "retrieval" else r.get("pass"),
                          "cells": {c["key"]: r.get(c["key"]) for c in cols}, "detail": r}
                         for i, r in enumerate(rows)],
            })
    if _num(raw.get("claude_cost_usd")):
        metrics["claude_cost_usd"] = raw["claude_cost_usd"]
    cfg = raw.get("config") or {}
    u = raw.get("usage") or {}
    n = raw.get("n_cases")
    if u:
        cost = _cost_block([{"role": "gen+judge", "model": u.get("model"), "tokens_in": u.get("in"),
                             "tokens_out": u.get("out"), "usd": raw.get("claude_cost_usd"), "batch": False}], n, "tokens")
    elif _num(raw.get("claude_cost_usd")):
        cost = _cost_block([{"role": "gen+judge", "model": cfg.get("gen_model") or "claude-sonnet-5-5",
                             "usd": raw["claude_cost_usd"], "batch": False}], n, "cost_only",
                           "Lần chạy cũ không ghi model — giả định model mặc định claude-sonnet-5-5.")
    else:
        cost = _cost_block([], n, "none")
    metrics.update(_cost_metrics(cost))
    return {"cost": cost, "metrics": metrics, "variant": None, "config": cfg, "tables": tables, "groups": groups}


# ------------------------------------------------------------------ pytest (junit XML → JSON chung)
def pytest_postprocess(raw_path: str, out_path: str) -> None:
    root = ET.parse(raw_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    rows = []
    for s in suites:
        for tc in s.iter("testcase"):
            status, msg = "passed", ""
            for tag in ("failure", "error", "skipped"):
                el = tc.find(tag)
                if el is not None:
                    status = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                    msg = (el.get("message") or "") + ("\n" + el.text if el.text else "")
                    break
            cls = tc.get("classname") or ""
            parts = cls.split(".")
            mod = next((i for i, x in enumerate(parts) if x.startswith("test_")), len(parts) - 1)
            rows.append({"id": f"{cls}::{tc.get('name')}", "file": "/".join(parts[: mod + 1]) + ".py",
                         "name": tc.get("name"), "status": status, "time": float(tc.get("time") or 0),
                         "message": msg[:4000]})
    n = len(rows)
    cnt = {st: sum(r["status"] == st for r in rows) for st in ("passed", "failed", "error", "skipped")}
    ran = n - cnt["skipped"]
    out = {"summary": {"tests": n, **cnt, "pass_rate": (cnt["passed"] / ran) if ran else None,
                       "duration_s": round(sum(r["time"] for r in rows), 2)}, "rows": rows}
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.remove(raw_path)


def norm_pytest(raw: dict) -> dict:
    rows = raw.get("rows") or []
    return {
        "cost": _cost_block([], (raw.get("summary") or {}).get("tests"), "free", "Unit test chạy offline, không gọi API."),
        "metrics": {**_flatten(raw.get("summary") or {}), "cost.total_usd": 0.0},
        "variant": None, "config": {}, "tables": [],
        "groups": [{"id": "tests", "title": "Từng test", "columns": [
            {"key": "status", "label": "Trạng thái pytest", "fmt": "text"},
            {"key": "time", "label": "Thời gian", "fmt": "sec"},
        ], "rows": [{"id": r["id"], "label": r["name"], "category": r["file"],
                     "pass": None if r["status"] == "skipped" else r["status"] == "passed",
                     "cells": {"status": r["status"], "time": r["time"]},
                     "detail": {"message": r["message"]} if r["message"] else {}} for r in rows]}],
    }


class PytestSuite(Suite):
    def out_args(self, path: str) -> list[str]:
        return [f"--junitxml={path}"]


# ------------------------------------------------------------------ registry
_RET_PIPES = ("hybrid_new", "bm25_only")
_OPENAI_CHAT = ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini"]
_CLAUDE = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]
_COST_SPECS = [
    MetricSpec("cost.total_usd", "Chi phí lần chạy", "usd", "down", True, group="Chi phí"),
    MetricSpec("cost.per_case_usd", "Chi phí / case", "usd", "down", group="Chi phí"),
    MetricSpec("cost.tokens_in", "Token vào", "int", "down", group="Chi phí"),
    MetricSpec("cost.tokens_out", "Token ra", "int", "down", group="Chi phí"),
]

SUITES: list[Suite] = [
    Suite(
        id="retrieval", kind="benchmark", name="Truy hồi (retrieval)",
        description="Pooled LLM-judge trên câu hỏi thật + known-item trên câu tổng hợp. "
                    "So sánh pipeline chatbot (hybrid_new) với mốc BM25.",
        file_prefix="nogt_eval_", script=["evaluation/eval_retrieval_nogt.py"],
        requires=["Milvus", "Neo4j", "MySQL", "OpenAI", "Claude (nếu chấm judge)"],
        cost_hint="Judge Claude tốn tiền theo --budget (mặc định trần $3); bật “Bỏ qua judge” để chỉ chạy known-item.",
        params=[
            Param("skip_judge", "Bỏ qua LLM-judge (chỉ known-item)", "bool", True),
            Param("synthetic", "Số câu known-item", "int", 100, min=0, max=500),
            Param("judge_model", "Model judge", "select", "claude-sonnet-5-5", options=_CLAUDE),
            Param("judge_effort", "Effort judge", "select", "low", options=["low", "medium", "high"]),
            Param("budget", "Trần chi phí judge (USD)", "float", 1.0, min=0, max=20),
            Param("limit", "Giới hạn số câu hỏi thật", "int", None, min=1, max=1000, help="Để trống = tất cả"),
            Param("dry_run", "Dry-run (chỉ ước tính chi phí)", "bool", False),
        ],
        metrics=[
            *[MetricSpec(f"known_item.{p}.{m}", f"{p} · {m}", "ratio", "up",
                         headline=(p == "hybrid_new" and m in ("Hit@1", "Hit@10", "MRR@10")), group="Known-item")
              for p in _RET_PIPES for m in ("Hit@1", "Hit@5", "Hit@10", "MRR@10")],
            *[MetricSpec(f"judge.{p}.{m}", f"{p} · {m}", "ratio", "up",
                         headline=(p == "hybrid_new" and m == "nDCG@10"), group="LLM-judge")
              for p in _RET_PIPES for m in ("P@5", "P@10", "nDCG@10", "MRR@10(rel=2)", "Top1")],
            *_COST_SPECS,
        ],
        model_params={"judge_model": "judge"},
        normalize=norm_retrieval,
    ),
    Suite(
        id="tool_calls", kind="eval", name="Gọi tool của agent",
        description="Agent có gọi đúng tool, đúng tham số, đúng lúc trên 23 case có kỳ vọng ghi sẵn.",
        file_prefix="tool_eval_", script=["evaluation/eval_tool_calls.py"], variant_label="Model", variant_param="model",
        requires=["Milvus", "Neo4j", "MySQL", "OpenAI", "Tavily"],
        cost_hint="Khoảng $0.1 (gpt-4o-mini) – $1 (gpt-4.1) mỗi lần lặp.",
        params=[
            Param("model", "Model agent", "select", "gpt-4.1", options=_OPENAI_CHAT),
            Param("repeat", "Số lần lặp mỗi case", "int", 1, min=1, max=10,
                  help="Agent không tất định — nên ≥ 3 để có mốc tin cậy"),
            Param("only", "Chỉ nhóm (category)", "str", None),
            Param("ids", "Chỉ các case (id, phân cách dấu phẩy)", "str", None),
            Param("max_iterations", "Số vòng tối đa", "int", 6, min=1, max=12),
        ],
        metrics=[
            MetricSpec("case_pass", "Tỉ lệ case đạt", "pct", "up", True),
            MetricSpec("must_call_recall", "Gọi tool bắt buộc", "pct", "up"),
            MetricSpec("should_call_recall", "Gọi tool nên gọi", "pct", "up"),
            MetricSpec("first_tool_acc", "Tool đầu tiên đúng", "pct", "up"),
            MetricSpec("forced_web_acc", "Buộc web đúng", "pct", "up"),
            MetricSpec("answer_check", "Câu trả lời đủ từ khoá", "pct", "up"),
            MetricSpec("forbidden_rate", "Gọi tool bị cấm", "pct", "down"),
            MetricSpec("invalid_id_rate", "Bịa mã điều luật", "pct", "down", True),
            MetricSpec("empty_arg_rate", "Thiếu tham số", "pct", "down"),
            MetricSpec("redundant_rate", "Gọi trùng", "pct", "down"),
            MetricSpec("over_budget_rate", "Vượt trần số lượt", "pct", "down"),
            MetricSpec("max_iter_rate", "Chạm trần vòng lặp", "pct", "down"),
            MetricSpec("avg_calls", "Số lượt gọi TB", "num", "none"),
            MetricSpec("avg_latency_s", "Thời gian TB", "sec", "down", True),
            *_COST_SPECS,
            MetricSpec("cost_usd", "Chi phí (script)", "usd", "down"),
            MetricSpec("cases", "Số case", "int", "none"),
        ],
        model_params={"model": "agent"}, scale_params=["repeat"],
        normalize=norm_tool_calls,
    ),
    Suite(
        id="memory", kind="eval", name="Bộ nhớ người dùng",
        description="Trích xuất / cập nhật / xoá ký ức, không lộ thông tin nhạy cảm, không trùng lặp.",
        file_prefix="memory_eval_", script=["evaluation/eval_memory.py"],
        requires=["Milvus", "MongoDB", "OpenAI"], cost_hint="Vài cent mỗi lần.",
        params=[
            Param("model", "Model trích xuất / cập nhật ký ức", "select", "gpt-4o-mini", options=_OPENAI_CHAT),
            Param("repeat", "Số lần lặp", "int", 1, min=1, max=10),
            Param("only", "Chỉ nhóm (category)", "str", None),
        ],
        variant_label="Model", variant_param="model", model_params={"model": "memory"}, scale_params=["repeat"],
        metrics=[
            MetricSpec("case_pass", "Tỉ lệ case đạt", "pct", "up", True),
            MetricSpec("sensitive_leaks", "Lộ thông tin nhạy cảm", "int", "down", True),
            MetricSpec("duplicate_or_stale", "Trùng / lỗi thời", "int", "down", True),
            MetricSpec("avg_latency_s", "Thời gian TB", "sec", "down"),
            *_COST_SPECS,
            MetricSpec("cases", "Số case", "int", "none"),
        ],
        normalize=norm_memory,
    ),
    Suite(
        id="exceptions", kind="eval", name="Ngoại lệ “cho phép nhưng cấm”",
        description="Khi điều chính cho phép, kết quả có kèm khoản cấm / ngoại lệ liên quan không (tắt/bật kiểm tra ngoại lệ).",
        file_prefix="exception_eval_", script=["evaluation/eval_exceptions.py"],
        requires=["Milvus", "Neo4j", "OpenAI", "Claude"],
        cost_hint="Claude sinh case + judge, trần theo --budget (mặc định $1.5).",
        params=[
            Param("n", "Số case", "int", 30, min=1, max=200),
            Param("seed", "Seed", "int", 11),
            Param("answers", "Đo cả câu trả lời (agent + judge)", "bool", False),
            Param("budget", "Trần chi phí Claude (USD)", "float", 1.5, min=0, max=20),
            Param("gen_model", "Model sinh case + judge", "select", "claude-sonnet-5-5", options=_CLAUDE),
        ],
        model_params={"gen_model": "gen+judge"}, scale_params=["n"],
        metrics=[
            MetricSpec("retrieval.B (ngoại lệ) có — BẬT kiểm tra ngoại lệ", "Có ngoại lệ — BẬT", "pct", "up", True),
            MetricSpec("retrieval.B (ngoại lệ) có — TẮT kiểm tra ngoại lệ", "Có ngoại lệ — TẮT", "pct", "up"),
            MetricSpec("retrieval.B có | A có — BẬT", "Có ngoại lệ khi có điều chính — BẬT", "pct", "up", True),
            MetricSpec("retrieval.A (điều chính) có trong kết quả", "Có điều chính", "pct", "up", True),
            MetricSpec("retrieval.số kết quả TB — TẮT", "Số kết quả TB — TẮT", "num", "none"),
            MetricSpec("retrieval.số kết quả TB — BẬT", "Số kết quả TB — BẬT", "num", "none"),
            *_COST_SPECS,
        ],
        normalize=norm_exceptions,
    ),
    PytestSuite(
        id="unit_tests", kind="test", name="Unit test (pytest)",
        description="Toàn bộ tests/ — chạy offline, không gọi dịch vụ thật.",
        file_prefix="pytest_", script=["-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
        cost_hint="Miễn phí.", requires=[], variant_label="Lọc -k", variant_param="k",
        params=[Param("k", "Lọc test (-k)", "str", None, flag="-k")],
        metrics=[
            MetricSpec("pass_rate", "Tỉ lệ đạt", "pct", "up", True),
            MetricSpec("failed", "Trượt", "int", "down", True),
            MetricSpec("error", "Lỗi", "int", "down", True),
            MetricSpec("tests", "Số test", "int", "none", True),
            MetricSpec("passed", "Đạt", "int", "up"),
            MetricSpec("skipped", "Bỏ qua", "int", "none"),
            MetricSpec("duration_s", "Thời gian", "sec", "down"),
            MetricSpec("cost.total_usd", "Chi phí", "usd", "down"),
        ],
        normalize=norm_pytest, postprocess=pytest_postprocess,
    ),
]

SUITES_BY_ID = {s.id: s for s in SUITES}


def suite_for_file(name: str) -> Optional[Suite]:
    for s in SUITES:
        if name.startswith(s.file_prefix) and name.endswith(".json"):
            return s
    return None
