"""
Fixture dùng chung cho unit test agent/retrieval.

Nguyên tắc: KHÔNG gọi dịch vụ thật (OpenAI, Milvus, MySQL, Neo4j, Tavily). Mọi phụ thuộc
được thay bằng bản giả để test chạy offline, nhanh, lặp lại được.
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Client SDK được tạo ngay khi import module → cần key giả (không bao giờ dùng tới).
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")
os.environ.setdefault("TAVILY_API_KEY", "tvly-test-dummy")

# retrieve/tavily_fallback.py tạo TavilyClient + OpenAI ở cấp module và build_graph import nó
# ngay đầu file → thay bằng module giả để import retriever không chạm mạng.
_fake_tavily = types.ModuleType("retrieve.tavily_fallback")
_fake_tavily.search_tavily = lambda query, top_k=5: []
_fake_tavily.search_tavily_realtime = lambda query: []
_fake_tavily.decompose_query = lambda query: {"legal_query": query}
_fake_tavily.is_context_sufficient = lambda candidates, query="", threshold=0.6: True
sys.modules["retrieve.tavily_fallback"] = _fake_tavily


# ── Dữ liệu mẫu ──────────────────────────────────────────────────────────────
ARTICLES: dict[str, dict[str, Any]] = {
    "M_XEDAP": {
        "mapc": "M_XEDAP",
        "ten": "Điều 39.13.NĐ.75.8. Xử phạt người điều khiển xe đạp, xe đạp máy (kể cả xe đạp điện)",
        "noidung": "3. Phạt tiền từ 200.000 đồng đến 300.000 đồng: d) Người điều khiển xe đạp máy "
                   "(kể cả xe đạp điện) không đội mũ bảo hiểm cho người đi mô tô, xe máy.",
        "tenchuong": "Chương II", "tendemuc": "Xử lý vi phạm hành chính", "tenchude": "Trật tự, an toàn xã hội",
        "vbqppl": "(Điều 8 Nghị định số 100/2019/NĐ-CP, có hiệu lực thi hành kể từ ngày 01/01/2020)",
    },
    "M_KETHON": {
        "mapc": "M_KETHON",
        "ten": "Điều 8.4.LQ.8. Điều kiện kết hôn",
        "noidung": "1. Nam, nữ kết hôn với nhau phải tuân theo các điều kiện sau: a) Nam từ đủ 20 tuổi "
                   "trở lên, nữ từ đủ 18 tuổi trở lên;",
        "tenchuong": "Chương II", "tendemuc": "Hôn nhân và gia đình", "tenchude": "Dân sự",
        "vbqppl": "(Điều 8 Luật số 52/2014/QH13, có hiệu lực thi hành kể từ ngày 01/01/2015)",
    },
    "M_THAISAN": {
        "mapc": "M_THAISAN",
        "ten": "Điều 20.2.LQ.137. Bảo vệ thai sản",
        "noidung": "3. Người sử dụng lao động không được sa thải hoặc đơn phương chấm dứt hợp đồng lao "
                   "động đối với người lao động vì lý do kết hôn, mang thai, nghỉ thai sản.",
        "tenchuong": "Chương X", "tendemuc": "Lao động", "tenchude": "Lao động",
        "vbqppl": "(Điều 137 Bộ luật số 45/2019/QH14, có hiệu lực thi hành kể từ ngày 01/01/2021)",
    },
}


class FakeNeo4jDriver:
    """Neo4j rỗng (giống môi trường hiện tại): mọi session trả về không có dòng nào."""

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, *a, **k):
            return FakeNeo4jDriver._Result()

    class _Result(list):
        def single(self):
            return None

    def session(self):
        return FakeNeo4jDriver._Session()


@pytest.fixture
def articles():
    return {k: dict(v) for k, v in ARTICLES.items()}


@pytest.fixture
def fake_mysql(monkeypatch, articles):
    """Thay MySQL fallback của agent bằng dict ARTICLES. Trả về list các lượt gọi để assert."""
    import services.agentic_rag as A

    calls: list[list[str]] = []

    def fetch(mapcs):
        calls.append(list(mapcs))
        return [dict(articles[m]) for m in mapcs if m in articles]

    monkeypatch.setattr(A, "_fetch_dieu_mysql", fetch)
    return calls


@pytest.fixture
def fake_retriever():
    """GraphRAGRetriever thật (giữ _rerank thật) nhưng nguồn tìm kiếm là kịch bản cố định."""
    from retrieve.build_graph import GraphRAGRetriever

    class FakeRetriever(GraphRAGRetriever):
        def __init__(self):
            super().__init__(neo4j_driver=FakeNeo4jDriver(), milvus_collection=None, openai_client=None)
            self.fused: list[dict] = []          # kết quả _hybrid_search trả về
            self.graph_rows: list[dict] = []     # kết quả _graph_expand trả về
            self.calls: list[tuple] = []

        def _hybrid_search(self, query, top_k, pool=50, mode=None, weights=None):
            self.calls.append(("hybrid", query, top_k))
            return [dict(d) for d in self.fused[:top_k]]

        def _graph_expand(self, seed_mapcs, hop):
            self.calls.append(("graph", tuple(seed_mapcs), hop))
            return [dict(r) for r in self.graph_rows]

    return FakeRetriever()


# ── OpenAI chat giả theo kịch bản ────────────────────────────────────────────
def tool_call(name: str, args: dict | str, call_id: str = "call_1"):
    arguments = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return SimpleNamespace(id=call_id, type="function",
                           function=SimpleNamespace(name=name, arguments=arguments))


def assistant(content: str | None = None, tool_calls: list | None = None):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=tool_calls or None))])


class ScriptedChatClient:
    """chat.completions.create trả lần lượt các response đã soạn; ghi lại mọi request."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # Chụp bản sao: agent tiếp tục append vào cùng list messages sau mỗi lượt
        self.requests.append({**kwargs, "messages": [dict(m) for m in kwargs.get("messages", [])]})
        if not self._responses:
            raise AssertionError("Agent gọi LLM nhiều hơn kịch bản")
        return self._responses.pop(0)


@pytest.fixture
def scripted_client():
    return ScriptedChatClient
