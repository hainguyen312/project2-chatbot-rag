"""
Rate limit theo người dùng + IP cho endpoint gọi LLM (SURVEY bước 5).

Cửa sổ trượt trong bộ nhớ (backend chạy 1 worker uvicorn). Khởi động lại → đếm lại từ đầu;
giới hạn bền vững là trần chung/ngày trong MySQL (services/usage_cap.py).

Quy tắc mỗi loại: "số/giây[,số/giây…]" — vd "6/60,60/3600" = 6 lần/phút và 60 lần/giờ.
Ghi đè bằng env RATE_LIMIT_<LOẠI>_USER / RATE_LIMIT_<LOẠI>_IP (vd RATE_LIMIT_QUESTION_USER).
Chuỗi rỗng "off" → tắt quy tắc đó.
"""
from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from typing import Callable, Dict, List, Mapping, Optional, Tuple

Rule = Tuple[int, float]  # (số lần tối đa, cửa sổ giây)

DEFAULTS: Dict[str, Dict[str, str]] = {
    # chat, stream, rà soát văn bản — tốn nhất
    "question": {"user": "6/60,60/3600", "ip": "12/60,120/3600"},
    "tts": {"user": "20/60", "ip": "40/60"},
    "stt": {"user": "10/60", "ip": "20/60"},
    "title": {"user": "10/60", "ip": "20/60"},
}

MESSAGE = "Bạn thao tác hơi nhanh. Vui lòng thử lại sau {seconds} giây."
_MAX_KEYS = 50_000


def parse_rules(spec: str) -> List[Rule]:
    spec = (spec or "").strip().lower()
    if spec in ("", "off", "0"):
        return []
    rules: List[Rule] = []
    for part in spec.split(","):
        n, _, w = part.strip().partition("/")
        count, window = int(n), float(w)
        if count <= 0 or window <= 0:
            raise ValueError(part)
        rules.append((count, window))
    return rules


def rules_from_env(env: Optional[Mapping[str, str]] = None) -> Dict[str, Dict[str, List[Rule]]]:
    env = os.environ if env is None else env
    out: Dict[str, Dict[str, List[Rule]]] = {}
    for kind, scopes in DEFAULTS.items():
        out[kind] = {}
        for scope, default in scopes.items():
            key = f"RATE_LIMIT_{kind.upper()}_{scope.upper()}"
            raw = env.get(key)
            try:
                out[kind][scope] = parse_rules(raw if raw is not None else default)
            except ValueError:
                print(f"[RateLimit] {key}={raw!r} không hợp lệ — dùng mặc định {default}")
                out[kind][scope] = parse_rules(default)
    return out


class RateLimiter:
    def __init__(self, rules: Dict[str, Dict[str, List[Rule]]],
                 clock: Callable[[], float] = time.monotonic):
        self.rules, self.clock = rules, clock
        self._hits: Dict[Tuple[str, str, str], deque] = {}
        self._lock = threading.Lock()

    def check(self, kind: str, user: Optional[str], ip: Optional[str]) -> int:
        """0 = cho qua (và ghi nhận lượt); >0 = số giây cần chờ (không ghi nhận)."""
        now = self.clock()
        scopes = self.rules.get(kind, {})
        keys = [(scope, ident) for scope, ident in (("user", user), ("ip", ip)) if ident and scopes.get(scope)]
        with self._lock:
            wait = 0.0
            for scope, ident in keys:
                q = self._hits.get((kind, scope, ident))
                if not q:
                    continue
                longest = max(w for _, w in scopes[scope])
                while q and q[0] <= now - longest:
                    q.popleft()
                for count, window in scopes[scope]:
                    recent = [t for t in q if t > now - window]
                    if len(recent) >= count:
                        wait = max(wait, recent[-count] + window - now)
            if wait > 0:
                return max(1, math.ceil(wait))
            for scope, ident in keys:
                self._hits.setdefault((kind, scope, ident), deque()).append(now)
            if len(self._hits) > _MAX_KEYS:
                self._gc(now)
        return 0

    def _gc(self, now: float) -> None:
        longest = max((w for sc in self.rules.values() for rs in sc.values() for _, w in rs), default=0)
        for k in [k for k, q in self._hits.items() if not q or q[-1] <= now - longest]:
            del self._hits[k]
