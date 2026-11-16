"""
Mọi endpoint gọi LLM trong rag_api.py phải có tham số `= Depends(require_login)`.

Kiểm bằng AST (không import rag_api — import sẽ kết nối DB, tải model). Thêm endpoint gọi LLM
mới → thêm vào LLM_ENDPOINTS; test cũng báo nếu một endpoint có gọi client OpenAI mà quên gắn.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "rag_api.py"
TREE = ast.parse(SRC.read_text(encoding="utf-8"))

# (method, path) → gọi LLM (OpenAI chat / tts / whisper) trực tiếp hoặc qua agent
LLM_ENDPOINTS = {
    ("post", "/rag/chat"),
    ("post", "/rag/stream"),
    ("post", "/chat/title"),
    ("post", "/review/analyze"),
    ("post", "/tts"),
    ("post", "/stt"),
}


def _endpoints():
    for node in TREE.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app"
                    and dec.args and isinstance(dec.args[0], ast.Constant)):
                yield (dec.func.attr, dec.args[0].value), node


def _has_require_login(fn) -> bool:
    defaults = fn.args.defaults + [d for d in fn.args.kw_defaults if d is not None]
    return any(
        isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "Depends"
        and d.args and isinstance(d.args[0], ast.Name) and d.args[0].id == "require_login"
        for d in defaults
    )


ENDPOINTS = dict(_endpoints())


@pytest.mark.parametrize("key", sorted(LLM_ENDPOINTS))
def test_llm_endpoint_requires_login(key):
    assert key in ENDPOINTS, f"không tìm thấy endpoint {key}"
    assert _has_require_login(ENDPOINTS[key]), f"{key} gọi LLM nhưng không có Depends(require_login)"


def test_require_login_defined_before_first_use():
    names = [n.name for n in TREE.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    first_use = min(names.index(fn.name) for k, fn in ENDPOINTS.items() if k in LLM_ENDPOINTS)
    assert names.index("require_login") < first_use


def test_no_unlisted_endpoint_calls_openai_directly():
    """Endpoint gọi trực tiếp *.audio.* / *.chat.completions / generate_chat_title mà không được liệt kê."""
    markers = ("audio.speech", "audio.transcriptions", "chat.completions", "generate_chat_title",
               "run_agentic_rag", "review_events")
    for key, fn in ENDPOINTS.items():
        src = ast.get_source_segment(SRC.read_text(encoding="utf-8"), fn) or ""
        if any(m in src for m in markers):
            assert key in LLM_ENDPOINTS, f"{key} có vẻ gọi LLM — thêm vào LLM_ENDPOINTS + require_login"
