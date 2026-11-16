"""data_processing/seed_data_batch: MySQL/Milvus theo biến môi trường (docker compose), không hardcode."""
from __future__ import annotations

import importlib
import sys

import pytest

pytest.importorskip("pymilvus")


@pytest.fixture
def seed(monkeypatch):
    sys.modules.pop("data_processing.seed_data_batch", None)
    mod = importlib.import_module("data_processing.seed_data_batch")
    calls = {}

    class Cur:
        def execute(self, sql):
            calls["sql"] = sql

        def fetchall(self):
            return [{"mapc": "M1", "tendieu": "Điều 1"}]

        def close(self):
            pass

    class Conn:
        def cursor(self, *_):
            return Cur()

        def close(self):
            pass

    monkeypatch.setattr(mod.pymysql, "connect", lambda **kw: calls.setdefault("kw", kw) and Conn())
    return mod, calls


def test_mysql_from_env(seed, monkeypatch):
    mod, calls = seed
    for k, v in {"MYSQL_HOST": "mysql", "MYSQL_PORT": "3307", "MYSQL_USER": "law_app",
                 "MYSQL_PASSWORD": "pw", "MYSQL_DATABASE": "law"}.items():
        monkeypatch.setenv(k, v)
    assert mod.load_data_from_mysql() == [{"mapc": "M1", "tendieu": "Điều 1"}]
    assert calls["kw"] == {"host": "mysql", "port": 3307, "user": "law_app", "password": "pw",
                           "database": "law", "charset": "utf8mb4"}


def test_embeds_only_article_title(seed, monkeypatch):
    mod, _ = seed
    seen = {}
    monkeypatch.setattr(mod, "embed_batch", lambda texts: seen.setdefault("t", texts) and [[0.0]] * len(texts))
    mod.collection = type("Col", (), {"insert": lambda self, data: seen.setdefault("ins", data)})()
    mod.insert_batch([{"tendieu": "  Điều 8. Điều kiện kết hôn ", "noidung": ["dài…"]}], 0, [])
    assert seen["t"] == ["Điều 8. Điều kiện kết hôn"]
