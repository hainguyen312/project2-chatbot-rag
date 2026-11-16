"""services/health: /health thật — critical lỗi → ok=False (503), không critical chỉ báo."""
from __future__ import annotations

import threading

from services import health as H


def _ok(detail=None):
    return lambda: detail


def _boom(msg="down"):
    def f():
        raise ConnectionError(msg)
    return f


def test_all_ok():
    ok, res = H.run_checks({"a": (_ok("x"), True), "b": (_ok(), False)})
    assert ok is True
    assert res["a"] == {"ok": True, "detail": "x", "ms": res["a"]["ms"], "critical": True}
    assert res["b"]["ok"] is True and "detail" not in res["b"]


def test_critical_failure_makes_not_ok():
    ok, res = H.run_checks({"mysql": (_boom("refused"), True), "bm25": (_ok("ready"), False)})
    assert ok is False
    assert res["mysql"]["ok"] is False
    assert res["mysql"]["error"] == "ConnectionError: refused"


def test_non_critical_failure_keeps_ok():
    ok, res = H.run_checks({"mysql": (_ok(), True), "mongodb": (_boom(), False)})
    assert ok is True
    assert res["mongodb"]["ok"] is False and res["mongodb"]["critical"] is False


def test_timeout_is_failure_and_does_not_block():
    release = threading.Event()
    try:
        ok, res = H.run_checks({"neo4j": (lambda: release.wait(5), True)}, timeout=0.2)
        assert ok is False
        assert res["neo4j"]["error"].startswith("timeout")
        assert res["_elapsed_ms"]["ms"] < 2000
    finally:
        release.set()


def test_error_message_truncated():
    ok, res = H.run_checks({"x": (_boom("e" * 1000), True)})
    assert len(res["x"]["error"]) <= 200


# ── Hàm kiểm cụ thể ──────────────────────────────────────────────────────────
class _Cur:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        self.log.append(sql)

    def fetchone(self):
        return (1,)


class _Conn:
    def __init__(self):
        self.log, self.closed = [], False

    def cursor(self):
        return _Cur(self.log)

    def close(self):
        self.closed = True


def test_check_mysql_runs_select_and_closes():
    conn = _Conn()
    assert H.check_mysql(lambda: conn) == "SELECT 1 ok"
    assert conn.log == ["SELECT 1"] and conn.closed


def test_check_milvus():
    col = type("C", (), {"name": "phapdien_simple_tendieu"})()
    assert "phapdien_simple_tendieu" in H.check_milvus(col, lambda: "v2.4.14")
    ok, res = H.run_checks({"milvus": (lambda: H.check_milvus(None, lambda: "v2.4.14"), True)})
    assert ok is False and "collection" in res["milvus"]["error"]


def test_check_neo4j():
    calls = []
    drv = type("D", (), {"verify_connectivity": lambda self: calls.append(1)})()
    assert H.check_neo4j(drv) == "connected" and calls == [1]
    ok, _ = H.run_checks({"neo4j": (lambda: H.check_neo4j(None), True)})
    assert ok is False


def test_check_mongo_disabled_and_ping():
    assert H.check_mongo(None) == "disabled"
    pings = []
    admin = type("A", (), {"command": lambda self, c: pings.append(c)})()
    client = type("M", (), {"admin": admin})()
    assert H.check_mongo(client) == "ping ok" and pings == ["ping"]


def test_check_bm25_states():
    assert H.check_bm25(lambda: (True, False)) == "ready"
    assert H.check_bm25(lambda: (False, False)) == "warming"
    ok, res = H.run_checks({"bm25": (lambda: H.check_bm25(lambda: (False, True)), False)})
    assert ok is True and res["bm25"]["ok"] is False
