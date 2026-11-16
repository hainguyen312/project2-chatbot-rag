"""services/internal_secret: backend từ chối request thiếu/sai X-Internal-Secret (bước 5e)."""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from services.internal_secret import InternalSecretMiddleware

SECRET = "x" * 40


async def ok(request):
    return JSONResponse({"ok": True})


async def stream(request):
    async def gen():
        for i in range(3):
            yield f"data: {i}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


def make(secret=SECRET):
    app = Starlette(routes=[Route("/health", ok), Route("/rag/chat", ok, methods=["GET", "POST"]),
                            Route("/rag/stream", stream, methods=["POST"])])
    return InternalSecretMiddleware(app, secret=secret)


@pytest.fixture
def client():
    return TestClient(make())


def test_missing_secret_forbidden(client):
    r = client.post("/rag/chat")
    assert r.status_code == 403 and r.json() == {"detail": "Forbidden"}


@pytest.mark.parametrize("bad", ["", "sai", SECRET[:-1], SECRET + "x", SECRET.upper()])
def test_wrong_secret_forbidden(client, bad):
    assert client.post("/rag/chat", headers={"X-Internal-Secret": bad}).status_code == 403


def test_correct_secret_ok(client):
    assert client.post("/rag/chat", headers={"X-Internal-Secret": SECRET}).json() == {"ok": True}


def test_header_case_insensitive(client):
    assert client.post("/rag/chat", headers={"x-internal-secret": SECRET}).status_code == 200


def test_streaming_passes_through(client):
    r = client.post("/rag/stream", headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 200 and r.text == "data: 0\n\ndata: 1\n\ndata: 2\n\n"


def test_health_from_outside_needs_secret(client):
    # TestClient có client host "testclient" — không phải loopback
    assert client.get("/health").status_code == 403


def _call(mw, path, client_host, headers=()):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": list(headers), "client": (client_host, 1234), "server": ("x", 80), "scheme": "http",
             "http_version": "1.1", "root_path": ""}
    asyncio.run(mw(scope, receive, send))
    return sent[0]["status"]


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_health_from_loopback_exempt(host):
    assert _call(make(), "/health", host) == 200


def test_loopback_exempt_only_for_health():
    assert _call(make(), "/rag/chat", "127.0.0.1") == 403


def test_no_secret_configured_allows_all_dev():
    assert TestClient(make(secret="")).post("/rag/chat").status_code == 200


def test_rag_api_installs_middleware_with_env_secret():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "rag_api.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "add_middleware"]
    assert any(c.args and isinstance(c.args[0], ast.Name) and c.args[0].id == "InternalSecretMiddleware"
               and "BACKEND_SHARED_SECRET" in ast.unparse(c) for c in calls)
