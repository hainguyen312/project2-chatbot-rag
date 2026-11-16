"""
Mọi endpoint gọi LLM trong rag_api.py phải có tham số `= Depends(<guard>)`, guard ∈
{require_login, question_quota}; endpoint ĐẶT CÂU HỎI (chat, stream, rà soát) phải dùng
question_quota (trần chung/ngày), và question_quota phải tự phụ thuộc require_login.

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

# Endpoint tính vào trần câu hỏi/ngày
QUESTION_ENDPOINTS = {("post", "/rag/chat"), ("post", "/rag/stream"), ("post", "/review/analyze")}
GUARDS = {"require_login", "question_quota"}


def _endpoints():
    for node in TREE.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app"
                    and dec.args and isinstance(dec.args[0], ast.Constant)):
                yield (dec.func.attr, dec.args[0].value), node


def _depends_on(fn) -> set:
    """Tên các hàm dùng trong Depends(...) ở tham số của fn."""
    defaults = fn.args.defaults + [d for d in fn.args.kw_defaults if d is not None]
    return {
        d.args[0].id for d in defaults
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "Depends"
        and d.args and isinstance(d.args[0], ast.Name)
    }


def _has_require_login(fn) -> bool:
    return bool(_depends_on(fn) & GUARDS)


FUNCS = {n.name: n for n in TREE.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


ENDPOINTS = dict(_endpoints())


@pytest.mark.parametrize("key", sorted(LLM_ENDPOINTS))
def test_llm_endpoint_requires_login(key):
    assert key in ENDPOINTS, f"không tìm thấy endpoint {key}"
    assert _has_require_login(ENDPOINTS[key]), f"{key} gọi LLM nhưng không có Depends(require_login)"


@pytest.mark.parametrize("key", sorted(QUESTION_ENDPOINTS))
def test_question_endpoint_counts_daily_cap(key):
    assert "question_quota" in _depends_on(ENDPOINTS[key]), f"{key} phải dùng Depends(question_quota)"


def test_question_quota_requires_login():
    assert "require_login" in _depends_on(FUNCS["question_quota"])


def test_guards_defined_before_first_use():
    names = [n.name for n in TREE.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    first_use = min(names.index(fn.name) for k, fn in ENDPOINTS.items() if k in LLM_ENDPOINTS)
    for g in GUARDS:
        assert names.index(g) < first_use, g


def test_no_unlisted_endpoint_calls_openai_directly():
    """Endpoint gọi trực tiếp *.audio.* / *.chat.completions / generate_chat_title mà không được liệt kê."""
    markers = ("audio.speech", "audio.transcriptions", "chat.completions", "generate_chat_title",
               "run_agentic_rag", "review_events")
    for key, fn in ENDPOINTS.items():
        src = ast.get_source_segment(SRC.read_text(encoding="utf-8"), fn) or ""
        if any(m in src for m in markers):
            assert key in LLM_ENDPOINTS, f"{key} có vẻ gọi LLM — thêm vào LLM_ENDPOINTS + require_login"
