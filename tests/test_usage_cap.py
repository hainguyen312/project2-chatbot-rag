"""services/usage_cap: trần chung số câu hỏi/ngày (mặc định 300), vượt → không gọi LLM."""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

import pytest

from services import usage_cap as U


# ── MySQL giả: hiểu đúng các câu lệnh DailyCap dùng ─────────────────────────
class FakeDB:
    def __init__(self):
        self.rows: dict[str, int] = {}
        self.lock = threading.Lock()
        self.fail = False
        self.deadlocks = 0  # số lần UPDATE kế tiếp ném deadlock 1213


class FakeCursor:
    def __init__(self, db):
        self.db, self.rowcount, self._row = db, 0, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        if self.db.fail:
            raise RuntimeError("MySQL down")
        s = " ".join(sql.split())
        with self.db.lock:
            if s.startswith("CREATE TABLE"):
                return
            if s.startswith("INSERT IGNORE"):
                self.db.rows.setdefault(params[0], 0)
            elif s.startswith("UPDATE usage_daily"):
                if self.db.deadlocks:
                    self.db.deadlocks -= 1
                    raise RuntimeError(1213, "Deadlock found when trying to get lock")
                day, cap = params
                if self.db.rows.get(day, 0) < cap:
                    self.db.rows[day] += 1
                    self.rowcount = 1
                else:
                    self.rowcount = 0
            elif s.startswith("SELECT count"):
                v = self.db.rows.get(params[0])
                self._row = (v,) if v is not None else None
            else:
                raise AssertionError(f"SQL không mong đợi: {s}")

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def autocommit(self, on):
        self.db.autocommit = on

    def commit(self):
        pass

    def close(self):
        pass


def make(cap, db=None, now=datetime(2026, 11, 16, 3, 0, tzinfo=timezone.utc)):
    db = db or FakeDB()
    clock = {"now": now}
    c = U.DailyCap(lambda: FakeConn(db), cap, clock=lambda: clock["now"])
    return c, db, clock


def test_consume_until_cap_then_blocked():
    c, db, _ = make(3)
    assert [c.consume() for _ in range(3)] == [1, 2, 3]
    with pytest.raises(U.CapExceeded):
        c.consume()
    assert c.used_today() == 3  # lượt bị chặn không được đếm


def test_new_day_resets_vietnam_time():
    c, db, clock = make(1)
    # 16:59 UTC = 23:59 giờ VN ngày 16
    clock["now"] = datetime(2026, 11, 16, 16, 59, tzinfo=timezone.utc)
    c.consume()
    with pytest.raises(U.CapExceeded):
        c.consume()
    # 17:00 UTC = 00:00 giờ VN ngày 17 → ngày mới
    clock["now"] = datetime(2026, 11, 16, 17, 0, tzinfo=timezone.utc)
    assert c.consume() == 1
    assert set(db.rows) == {"2026-11-16", "2026-11-17"}


def test_cap_zero_blocks_everything_without_db():
    c = U.DailyCap(lambda: (_ for _ in ()).throw(AssertionError("không được chạm DB")), 0)
    with pytest.raises(U.CapExceeded):
        c.consume()


def test_uses_autocommit():
    c, db, _ = make(5)
    c.consume()
    assert db.autocommit is True


def test_deadlock_is_retried():
    c, db, _ = make(5)
    db.deadlocks = 2
    assert c.consume() == 1


def test_persistent_deadlock_fails_closed():
    c, db, _ = make(5)
    db.deadlocks = 99
    with pytest.raises(U.CapUnavailable):
        c.consume()


def test_db_down_fails_closed():
    c, db, _ = make(10)
    db.fail = True
    with pytest.raises(U.CapUnavailable):
        c.consume()


def test_connect_error_fails_closed():
    def boom():
        raise ConnectionError("refused")
    with pytest.raises(U.CapUnavailable):
        U.DailyCap(boom, 10).consume()


def test_concurrent_requests_never_exceed_cap():
    c, db, _ = make(50)
    ok, blocked = [], []

    def worker():
        for _ in range(20):
            try:
                c.consume()
                ok.append(1)
            except U.CapExceeded:
                blocked.append(1)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 50 and len(blocked) == 8 * 20 - 50
    assert sum(db.rows.values()) == 50


@pytest.mark.parametrize("raw, expected", [
    (None, 300), ("", 300), ("150", 150), ("0", 0), ("-5", 0), ("abc", 300), (" 42 ", 42),
])
def test_cap_from_env(raw, expected):
    env = {} if raw is None else {"DAILY_QUESTION_CAP": raw}
    assert U.cap_from_env(env) == expected


def test_today_vn():
    assert U.today_vn(datetime(2026, 11, 16, 16, 59, tzinfo=timezone.utc)) == "2026-11-16"
    assert U.today_vn(datetime(2026, 11, 16, 17, 0, tzinfo=timezone.utc)) == "2026-11-17"


# ── Tích hợp MySQL thật (tuỳ chọn): TEST_MYSQL_HOST/PORT/USER/PASSWORD/DATABASE ─────────
@pytest.mark.skipif(not os.getenv("TEST_MYSQL_HOST"), reason="không có TEST_MYSQL_HOST")
def test_real_mysql_atomic():
    import pymysql

    def connect():
        return pymysql.connect(
            host=os.environ["TEST_MYSQL_HOST"], port=int(os.getenv("TEST_MYSQL_PORT", "3306")),
            user=os.getenv("TEST_MYSQL_USER", "root"), password=os.getenv("TEST_MYSQL_PASSWORD", ""),
            database=os.getenv("TEST_MYSQL_DATABASE", "law"), charset="utf8mb4",
        )

    with connect() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS usage_daily")
        conn.commit()
    c = U.DailyCap(connect, 30)
    ok = []

    def worker():
        for _ in range(10):
            try:
                c.consume()
                ok.append(1)
            except U.CapExceeded:
                pass

    ts = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 30 and c.used_today() == 30
