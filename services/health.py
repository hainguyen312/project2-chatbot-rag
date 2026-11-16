"""
Health check thật cho /health: kiểm từng phụ thuộc song song, có timeout.

- Phụ thuộc "critical" (MySQL, Milvus, Neo4j) lỗi → ok=False → /health trả 503
  (docker HEALTHCHECK và health gate của deploy.sh dựa vào mã này).
- Phụ thuộc không critical (MongoDB Atlas, BM25 đang dựng nền) chỉ báo trạng thái,
  không làm 503: Atlas ở ngoài, rollback cũng không sửa được; BM25 có fallback vector-only.

Không phụ thuộc rag_api → test được với hàm kiểm giả.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, Mapping, Tuple

CheckFn = Callable[[], Any]  # ném exception = lỗi; trả str/dict = chi tiết (tuỳ chọn)

DEFAULT_TIMEOUT_S = 3.0
_MAX_ERR_CHARS = 200

# Pool dùng chung: check treo (mạng chết) không chặn request /health sau đó quá timeout.
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="health")


def run_checks(
    checks: Mapping[str, Tuple[CheckFn, bool]],
    timeout: float = DEFAULT_TIMEOUT_S,
) -> Tuple[bool, Dict[str, Dict[str, Any]]]:
    """Chạy song song {tên: (hàm kiểm, critical)}. Trả (ok, kết quả từng mục)."""
    started = time.monotonic()
    futures = {name: _POOL.submit(_timed, fn) for name, (fn, _) in checks.items()}
    wait(futures.values(), timeout=timeout)

    results: Dict[str, Dict[str, Any]] = {}
    ok = True
    for name, (_, critical) in checks.items():
        fut = futures[name]
        if not fut.done():
            res: Dict[str, Any] = {"ok": False, "error": f"timeout > {timeout:g}s"}
        else:
            res = fut.result()  # _timed không ném
        res["critical"] = critical
        results[name] = res
        if critical and not res["ok"]:
            ok = False
    results["_elapsed_ms"] = {"ok": True, "ms": round((time.monotonic() - started) * 1000)}
    return ok, results


def _timed(fn: CheckFn) -> Dict[str, Any]:
    t0 = time.monotonic()
    try:
        detail = fn()
        res: Dict[str, Any] = {"ok": True}
        if detail not in (None, True):
            res["detail"] = detail
    except Exception as e:  # noqa: BLE001 — mọi lỗi đều là "không khoẻ"
        msg = f"{type(e).__name__}: {e}"
        res = {"ok": False, "error": msg[:_MAX_ERR_CHARS]}
    res["ms"] = round((time.monotonic() - t0) * 1000)
    return res


# ── Hàm kiểm cụ thể (nhận phụ thuộc làm tham số để test được) ──────────────────

def check_mysql(connect: Callable[[], Any]) -> str:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    finally:
        conn.close()
    return "SELECT 1 ok"


def check_milvus(collection: Any, server_version: Callable[[], str]) -> str:
    version = server_version()
    if collection is None:
        raise RuntimeError("collection chưa load (Milvus chưa sẵn sàng lúc backend khởi động?)")
    return f"server {version}, collection {getattr(collection, 'name', '?')}"


def check_neo4j(driver: Any) -> str:
    if driver is None:
        raise RuntimeError("chưa có driver")
    driver.verify_connectivity()
    return "connected"


def check_mongo(mongo_client: Any) -> str:
    if mongo_client is None:
        return "disabled"
    mongo_client.admin.command("ping")
    return "ping ok"


def check_bm25(state: Callable[[], Tuple[bool, bool]]) -> str:
    """state() → (đã sẵn sàng, đã thất bại)."""
    ready, failed = state()
    if ready:
        return "ready"
    if failed:
        raise RuntimeError("không dựng được index — chạy vector-only")
    return "warming"
