"""retrieve/two_stage_search: kết nối Milvus theo MILVUS_HOST/PORT (trong compose là "milvus")."""
from __future__ import annotations

import importlib
import sys

import pytest

pymilvus = pytest.importorskip("pymilvus")


@pytest.fixture
def fresh_import(monkeypatch):
    calls = []
    monkeypatch.setattr(pymilvus.connections, "connect", lambda alias, **kw: calls.append((alias, kw)))

    class _NoCollection:
        def __init__(self, *a, **k):
            raise RuntimeError("không có Milvus trong test")

    monkeypatch.setattr(pymilvus, "Collection", _NoCollection)
    saved = sys.modules.pop("retrieve.two_stage_search", None)

    def _import():
        sys.modules.pop("retrieve.two_stage_search", None)
        return importlib.import_module("retrieve.two_stage_search")

    yield _import, calls
    sys.modules.pop("retrieve.two_stage_search", None)
    if saved is not None:
        sys.modules["retrieve.two_stage_search"] = saved


def test_uses_env(fresh_import, monkeypatch):
    _import, calls = fresh_import
    monkeypatch.setenv("MILVUS_HOST", "milvus")
    monkeypatch.setenv("MILVUS_PORT", "29530")
    mod = _import()
    assert calls == [("default", {"host": "milvus", "port": "29530"})]
    assert mod.collection is None  # không load được → server vẫn chạy, /health báo lỗi


def test_defaults_localhost(fresh_import, monkeypatch):
    _import, calls = fresh_import
    monkeypatch.delenv("MILVUS_HOST", raising=False)
    monkeypatch.delenv("MILVUS_PORT", raising=False)
    _import()
    assert calls == [("default", {"host": "localhost", "port": "19530"})]
