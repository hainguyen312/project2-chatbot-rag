"""
Kho lần chạy (run) đánh giá — dựa trên file, không cần DB.

  evaluation/results/<prefix>_<YYYYmmdd_HHMMSS>.json   kết quả (do script ghi; CLI hay UI đều vậy)
  evaluation/runs/<run_id>.meta.json                   chỉ có với lần chạy từ UI: trạng thái, tham số, người chạy
  evaluation/runs/<run_id>.log                         stdout + stderr của lần chạy từ UI

run_id = tên file kết quả bỏ đuôi .json, nên lần chạy từ CLI (chỉ có file kết quả) và từ UI dùng
chung một danh sách. Kết quả chuẩn hoá được cache theo (đường dẫn, mtime).

Khi cần scale (nhiều máy chạy, lịch sử lớn): thay lớp này bằng bản dùng MongoDB/Postgres + object
storage cho file kết quả, giữ nguyên các hàm public (list_runs / get_run / save_meta / …).
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from typing import Any, Optional

from . import pricing
from .suites import EVAL_DIR, SUITES_BY_ID, suite_for_file

RESULTS_DIR = os.path.join(EVAL_DIR, "results")
RUNS_DIR = os.path.join(EVAL_DIR, "runs")
_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,160}$")
_TS_RE = re.compile(r"(\d{8}_\d{6})$")

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def valid_id(run_id: str) -> bool:
    return bool(_ID_RE.match(run_id or "")) and ".." not in run_id


def result_path(run_id: str) -> str:
    return os.path.join(RESULTS_DIR, run_id + ".json")


def meta_path(run_id: str) -> str:
    return os.path.join(RUNS_DIR, run_id + ".meta.json")


def log_path(run_id: str) -> str:
    return os.path.join(RUNS_DIR, run_id + ".log")


def _ts_from_id(run_id: str) -> Optional[str]:
    m = _TS_RE.search(run_id)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S").isoformat()
    except ValueError:
        return None


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ meta (lần chạy từ UI)
def load_meta(run_id: str) -> Optional[dict]:
    return _read_json(meta_path(run_id))


def save_meta(meta: dict) -> None:
    os.makedirs(RUNS_DIR, exist_ok=True)
    tmp = meta_path(meta["id"]) + ".tmp"
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        os.replace(tmp, meta_path(meta["id"]))


def all_metas() -> dict[str, dict]:
    out = {}
    if os.path.isdir(RUNS_DIR):
        for name in os.listdir(RUNS_DIR):
            if name.endswith(".meta.json"):
                m = _read_json(os.path.join(RUNS_DIR, name))
                if m and m.get("id"):
                    out[m["id"]] = m
    return out


# ------------------------------------------------------------------ kết quả chuẩn hoá
def normalized(run_id: str, suite_id: str) -> Optional[dict]:
    path = result_path(run_id)
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    raw = _read_json(path)
    if raw is None:
        return None
    try:
        norm = SUITES_BY_ID[suite_id].normalize(raw)
    except Exception as e:  # file cũ lệch định dạng: vẫn liệt kê, kèm lỗi
        norm = {"metrics": {}, "variant": None, "config": {}, "tables": [], "groups": [],
                "parse_error": f"{type(e).__name__}: {e}"}
    _cache[path] = (mt, norm)
    return norm


def _variant_from_params(suite_id: str, meta: Optional[dict]) -> Optional[str]:
    vp = SUITES_BY_ID[suite_id].variant_param
    v = ((meta or {}).get("params") or {}).get(vp) if vp else None
    return str(v) if v not in (None, "") else None


def _summary(run_id: str, suite_id: str, meta: Optional[dict]) -> dict:
    norm = normalized(run_id, suite_id) if (not meta or meta.get("status") == "done") else None
    created = (meta or {}).get("created_at") or _ts_from_id(run_id)
    return {
        "id": run_id,
        "suite": suite_id,
        "status": (meta or {}).get("status", "done"),
        "source": "ui" if meta and not meta.get("imported") else "cli",
        "created_at": created,
        "started_at": (meta or {}).get("started_at"),
        "finished_at": (meta or {}).get("finished_at"),
        "started_by": (meta or {}).get("started_by"),
        "params": (meta or {}).get("params"),
        "error": (meta or {}).get("error") or (norm or {}).get("parse_error"),
        "label": (meta or {}).get("label"),
        "variant": (norm or {}).get("variant") or _variant_from_params(suite_id, meta),
        "metrics": (norm or {}).get("metrics", {}),
    }


def list_runs(suite: str = "", limit: int = 200) -> list[dict]:
    metas = all_metas()
    ids: dict[str, str] = {}
    if os.path.isdir(RESULTS_DIR):
        for name in os.listdir(RESULTS_DIR):
            s = suite_for_file(name)
            if s:
                ids[name[:-5]] = s.id
    for rid, m in metas.items():
        if m.get("suite") in SUITES_BY_ID:
            ids[rid] = m["suite"]
    runs = [_summary(rid, sid, metas.get(rid)) for rid, sid in ids.items() if not suite or sid == suite]
    runs.sort(key=lambda r: r["created_at"] or "", reverse=True)
    return runs[:limit]


def get_run(run_id: str) -> dict:
    if not valid_id(run_id):
        raise KeyError(run_id)
    meta = load_meta(run_id)
    suite = (meta or {}).get("suite")
    if not suite:
        s = suite_for_file(run_id + ".json")
        if not s or not os.path.exists(result_path(run_id)):
            raise KeyError(run_id)
        suite = s.id
    out = _summary(run_id, suite, meta)
    norm = normalized(run_id, suite) or {}
    out.update(config=norm.get("config") or {}, tables=norm.get("tables") or [], groups=norm.get("groups") or [])
    cost = norm.get("cost")
    if cost:
        # "Nếu đổi model" tính lúc đọc → luôn theo bảng giá hiện hành, không gọi API.
        out["cost"] = {**cost, "what_if": pricing.what_if(cost.get("items") or [])}
    else:
        out["cost"] = None
    return out


def read_log(run_id: str, tail: int = 400) -> str:
    if not valid_id(run_id):
        raise KeyError(run_id)
    try:
        with open(log_path(run_id), encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return ""
    return "".join(lines[-tail:])


def update_label(run_id: str, label: str) -> None:
    meta = load_meta(run_id) if valid_id(run_id) else None
    if meta is None:
        # lần chạy từ CLI: tạo meta tối thiểu để lưu nhãn
        if not valid_id(run_id) or not suite_for_file(run_id + ".json") or not os.path.exists(result_path(run_id)):
            raise KeyError(run_id)
        meta = {"id": run_id, "suite": suite_for_file(run_id + ".json").id, "status": "done",
                "created_at": _ts_from_id(run_id), "imported": True}
    meta["label"] = (label or "").strip()[:120]
    save_meta(meta)
