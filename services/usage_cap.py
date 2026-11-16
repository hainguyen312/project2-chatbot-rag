"""
Trần chung số câu hỏi mỗi ngày cho TOÀN hệ thống (SURVEY bước 5): vượt trần → không gọi LLM.

- Đếm trong MySQL (bảng usage_daily) — sống qua khởi động lại; MySQL là dịch vụ bắt buộc.
- Tăng nguyên tử: UPDATE … SET count = count + 1 WHERE day = ? AND count < cap → nhiều
  request đồng thời không vượt trần. Autocommit từng câu (INSERT IGNORE + UPDATE trong cùng
  transaction gây deadlock 1213 khi đông — đã gặp khi test với MySQL 9.6 thật); deadlock /
  lock timeout còn sót thì thử lại.
- Ngày theo giờ Việt Nam (UTC+7, không có giờ mùa hè).
- cap = 0 → chặn hết (công tắc khẩn cấp). MySQL lỗi → coi như KHÔNG cho phép (đóng an toàn).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

VN_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")
DEFAULT_CAP = 300
_RETRY_ERRNOS = {1213, 1205}  # deadlock, lock wait timeout
_RETRIES = 3

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS usage_daily (
  day   DATE         NOT NULL PRIMARY KEY,
  count INT UNSIGNED NOT NULL DEFAULT 0
)
"""

CAP_MESSAGE = (
    "Hệ thống đã nhận đủ số câu hỏi tối đa cho hôm nay. "
    "Bạn vui lòng quay lại vào ngày mai nhé — xin cảm ơn!"
)
UNAVAILABLE_MESSAGE = "Hệ thống tạm thời không nhận câu hỏi mới. Vui lòng thử lại sau ít phút."


def cap_from_env(env: Optional[dict] = None) -> int:
    raw = ((env if env is not None else os.environ).get("DAILY_QUESTION_CAP") or "").strip()
    if not raw:
        return DEFAULT_CAP
    try:
        return max(0, int(raw))
    except ValueError:
        print(f"[UsageCap] DAILY_QUESTION_CAP={raw!r} không hợp lệ — dùng {DEFAULT_CAP}")
        return DEFAULT_CAP


def today_vn(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(VN_TZ).date().isoformat()


class CapExceeded(Exception):
    """Đã đủ trần hôm nay (status 429)."""


class CapUnavailable(Exception):
    """Không đếm được (MySQL lỗi) — không cho gọi LLM (status 503)."""


class DailyCap:
    def __init__(self, connect: Callable[[], Any], cap: int,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.connect, self.cap, self.clock = connect, cap, clock
        self._table_ready = False

    def _ensure_table(self, cur) -> None:
        if not self._table_ready:
            cur.execute(TABLE_DDL)
            self._table_ready = True

    def consume(self) -> int:
        """Lấy 1 lượt. Trả số đã dùng hôm nay (sau khi lấy); raise CapExceeded / CapUnavailable."""
        if self.cap <= 0:
            raise CapExceeded()
        day = today_vn(self.clock())
        last: Exception | None = None
        for _ in range(_RETRIES):
            try:
                allowed, used = self._consume_once(day)
            except CapUnavailable:
                raise
            except Exception as e:
                last = e
                if _errno(e) in _RETRY_ERRNOS:
                    continue
                break
            if not allowed:
                raise CapExceeded()
            return used
        print(f"[UsageCap] Lỗi MySQL khi đếm: {last}")
        raise CapUnavailable() from last

    def _consume_once(self, day: str) -> tuple[bool, int]:
        try:
            conn = self.connect()
        except Exception as e:
            print(f"[UsageCap] Không kết nối được MySQL: {e}")
            raise CapUnavailable() from e
        try:
            _autocommit(conn)
            with conn.cursor() as cur:
                self._ensure_table(cur)
                cur.execute("INSERT IGNORE INTO usage_daily (day, count) VALUES (%s, 0)", (day,))
                cur.execute(
                    "UPDATE usage_daily SET count = count + 1 WHERE day = %s AND count < %s",
                    (day, self.cap),
                )
                allowed = cur.rowcount == 1
                cur.execute("SELECT count FROM usage_daily WHERE day = %s", (day,))
                row = cur.fetchone()
            return allowed, (int(row[0]) if row else 0)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def used_today(self) -> int:
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                self._ensure_table(cur)
                cur.execute("SELECT count FROM usage_daily WHERE day = %s", (today_vn(self.clock()),))
                row = cur.fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()


def _errno(e: Exception) -> Optional[int]:
    args = getattr(e, "args", ())
    return args[0] if args and isinstance(args[0], int) else None


def _autocommit(conn) -> None:
    fn = getattr(conn, "autocommit", None)
    if callable(fn):
        fn(True)
