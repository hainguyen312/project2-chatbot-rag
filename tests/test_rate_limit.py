"""services/rate_limit: cửa sổ trượt theo user + IP; vượt → số giây phải chờ, không ghi nhận."""
from __future__ import annotations

import threading

import pytest

from services import rate_limit as R


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def limiter(user="3/60", ip="5/60"):
    c = Clock()
    rules = {"question": {"user": R.parse_rules(user), "ip": R.parse_rules(ip)}}
    return R.RateLimiter(rules, clock=c), c


def test_allows_up_to_limit_then_waits():
    rl, c = limiter()
    assert [rl.check("question", "u1", "1.1.1.1") for _ in range(3)] == [0, 0, 0]
    wait = rl.check("question", "u1", "1.1.1.1")
    assert 1 <= wait <= 60


def test_window_slides():
    rl, c = limiter()
    for _ in range(3):
        rl.check("question", "u1", "ip")
        c.t += 10
    assert rl.check("question", "u1", "ip") > 0  # t=1030: 3 lượt trong 60s
    c.t = 1000 + 60.5                            # lượt đầu (t=1000) hết hạn
    assert rl.check("question", "u1", "ip") == 0


def test_wait_seconds_is_accurate():
    rl, c = limiter(user="2/60")
    rl.check("question", "u1", None)
    c.t += 20
    rl.check("question", "u1", None)
    c.t += 5
    assert rl.check("question", "u1", None) == 35  # lượt đầu hết hạn sau 60 - 25 = 35s


def test_blocked_attempts_are_not_recorded():
    rl, c = limiter(user="1/60")
    rl.check("question", "u1", None)
    for _ in range(10):
        assert rl.check("question", "u1", None) > 0
    c.t += 61
    assert rl.check("question", "u1", None) == 0


def test_users_independent():
    rl, _ = limiter(user="1/60", ip="off")
    assert rl.check("question", "u1", "ip") == 0
    assert rl.check("question", "u2", "ip") == 0
    assert rl.check("question", "u1", "ip") > 0


def test_ip_limit_across_users():
    rl, _ = limiter(user="10/60", ip="3/60")
    assert [rl.check("question", f"u{i}", "9.9.9.9") for i in range(3)] == [0, 0, 0]
    assert rl.check("question", "u-moi", "9.9.9.9") > 0       # IP đã đủ
    assert rl.check("question", "u-moi", "8.8.8.8") == 0      # IP khác vẫn được


def test_multiple_windows():
    rl, c = limiter(user="2/10,3/100", ip="off")
    rl.check("question", "u", None); rl.check("question", "u", None)
    assert rl.check("question", "u", None) > 0   # 2/10s
    c.t += 11
    assert rl.check("question", "u", None) == 0  # lượt thứ 3 trong 100s
    c.t += 11
    assert rl.check("question", "u", None) > 0   # 3/100s


def test_unknown_kind_or_missing_ident_passes():
    rl, _ = limiter()
    assert rl.check("khac", "u", "ip") == 0
    assert all(rl.check("question", None, None) == 0 for _ in range(20))


def test_concurrent_exact():
    rl, _ = limiter(user="50/60", ip="off")
    ok = []
    def w():
        for _ in range(20):
            if rl.check("question", "u", None) == 0:
                ok.append(1)
    ts = [threading.Thread(target=w) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(ok) == 50


def test_gc_bounds_memory(monkeypatch):
    monkeypatch.setattr(R, "_MAX_KEYS", 10)
    rl, c = limiter(user="5/60", ip="off")
    for i in range(30):
        rl.check("question", f"u{i}", None)
    c.t += 61
    rl.check("question", "moi", None)  # vượt _MAX_KEYS → dọn khoá đã hết hạn
    assert len(rl._hits) <= 11


@pytest.mark.parametrize("spec, expected", [
    ("6/60,60/3600", [(6, 60.0), (60, 3600.0)]), ("off", []), ("", []), ("0", []), (" 3/1 ", [(3, 1.0)]),
])
def test_parse_rules(spec, expected):
    assert R.parse_rules(spec) == expected


@pytest.mark.parametrize("bad", ["abc", "5", "0/60", "5/0", "-1/60"])
def test_parse_rules_invalid(bad):
    with pytest.raises(ValueError):
        R.parse_rules(bad)


def test_rules_from_env_defaults_and_override():
    rules = R.rules_from_env({"RATE_LIMIT_TTS_USER": "1/1", "RATE_LIMIT_STT_IP": "off",
                              "RATE_LIMIT_TITLE_USER": "rác"})
    assert rules["question"]["user"] == [(6, 60.0), (60, 3600.0)]
    assert rules["tts"]["user"] == [(1, 1.0)]
    assert rules["stt"]["ip"] == []
    assert rules["title"]["user"] == [(10, 60.0)]  # giá trị hỏng → mặc định
    assert set(rules) == {"question", "tts", "stt", "title"}
