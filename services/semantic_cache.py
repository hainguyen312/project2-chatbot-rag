"""
Semantic Cache – lưu kết quả RAG vào MongoDB, tái sử dụng cho các câu hỏi tương đồng.

Cách hoạt động:
  1. Mỗi câu hỏi được embed thành vector 1536 chiều (text-embedding-3-small).
  2. Trước khi chạy agentic RAG, tìm entry trong cache có cosine similarity >= THRESHOLD.
  3. Nếu tìm thấy → trả về kết quả cache (cache_hit=True), bỏ qua toàn bộ pipeline RAG.
  4. Sau khi có kết quả mới → lưu vào cache với TTL 24 giờ (MongoDB TTL index).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import numpy as np
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False

try:
    from pymongo import MongoClient, ASCENDING
    from pymongo.errors import PyMongoError
except ImportError:
    MongoClient = None  # type: ignore
    PyMongoError = Exception  # type: ignore

MONGODB_URI = (os.getenv("MONGODB_URI") or "").strip()
MONGODB_DB  = (os.getenv("MONGODB_DB") or "chatbot_rag").strip()
MONGODB_TIMEOUT_MS = int((os.getenv("MONGODB_TIMEOUT_MS") or "8000").strip())

_CACHE_COLLECTION = "semantic_cache"
_SIMILARITY_THRESHOLD = float(os.getenv("CACHE_SIMILARITY_THRESHOLD", "0.92"))
_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "86400"))  # 24 giờ mặc định
# Giới hạn số entry đọc mỗi lần tìm kiếm (tránh scan toàn bộ collection)
_SCAN_LIMIT = int(os.getenv("CACHE_SCAN_LIMIT", "500"))


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Tính cosine similarity giữa hai vector."""
    if _NUMPY_OK:
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        norm_a = np.linalg.norm(va)
        norm_b = np.linalg.norm(vb)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(va, vb) / (norm_a * norm_b))
    # Fallback thuần Python nếu numpy không có
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticCache:
    """
    MongoDB-backed semantic cache cho kết quả RAG.

    Mỗi document trong collection có schema:
    {
        "embedding": [float, ...],          # vector 1536 chiều
        "result": { "meta": {...}, "answer": "..." },
        "created_at": datetime,
        "expires_at": datetime,             # TTL index trỏ vào đây
    }
    """

    def __init__(self):
        self._col = None
        self._initialized = False

    def _get_col(self):
        """Lazy init – chỉ kết nối MongoDB khi cần."""
        if self._initialized:
            return self._col
        self._initialized = True

        if not MONGODB_URI or MongoClient is None:
            print("[SemanticCache] MongoDB không được cấu hình – cache bị tắt")
            return None
        try:
            client = MongoClient(
                MONGODB_URI,
                serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS,
                connectTimeoutMS=MONGODB_TIMEOUT_MS,
                socketTimeoutMS=MONGODB_TIMEOUT_MS,
            )
            client.admin.command("ping")
            db = client[MONGODB_DB]
            col = db[_CACHE_COLLECTION]
            # TTL index: MongoDB tự xóa document khi expires_at đến
            col.create_index("expires_at", expireAfterSeconds=0, background=True)
            self._col = col
            print(f"[SemanticCache] Kết nối MongoDB thành công – collection: {_CACHE_COLLECTION}")
        except Exception as e:
            print(f"[SemanticCache] Không kết nối được MongoDB: {e}")
        return self._col

    def get(self, query_embedding: list[float]) -> dict[str, Any] | None:
        """
        Tìm kết quả cache cho query_embedding.

        Trả về dict {"meta": ..., "answer": ...} nếu tìm thấy entry có
        cosine similarity >= threshold, None nếu không.
        """
        col = self._get_col()
        if col is None:
            return None
        now = datetime.now(tz=timezone.utc)
        try:
            # Lấy các entry chưa hết hạn, sắp xếp mới nhất trước để ưu tiên
            # entry được tạo gần đây nhất (có thể chính xác hơn)
            docs = list(
                col.find(
                    {"expires_at": {"$gt": now}},
                    {"embedding": 1, "result": 1},
                ).sort("created_at", -1).limit(_SCAN_LIMIT)
            )
        except Exception as e:
            print(f"[SemanticCache] Lỗi khi tìm cache: {e}")
            return None

        best_sim = 0.0
        best_result = None
        for doc in docs:
            emb = doc.get("embedding")
            if not emb or len(emb) != len(query_embedding):
                continue
            sim = _cosine_sim(query_embedding, emb)
            if sim > best_sim:
                best_sim = sim
                best_result = doc.get("result")

        if best_sim >= _SIMILARITY_THRESHOLD and best_result is not None:
            print(f"[SemanticCache] Cache HIT – similarity={best_sim:.4f}")
            return best_result

        print(f"[SemanticCache] Cache MISS – best_sim={best_sim:.4f}")
        return None

    def set(self, query_embedding: list[float], result: dict[str, Any]) -> None:
        """Lưu kết quả RAG vào cache với TTL."""
        col = self._get_col()
        if col is None:
            return
        now = datetime.now(tz=timezone.utc)
        doc = {
            "embedding":  query_embedding,
            "result":     result,
            "created_at": now,
            "expires_at": now + timedelta(seconds=_TTL_SECONDS),
        }
        try:
            col.insert_one(doc)
            print("[SemanticCache] Đã lưu kết quả vào cache")
        except Exception as e:
            print(f"[SemanticCache] Lỗi khi lưu cache: {e}")


# Singleton dùng chung toàn app
semantic_cache = SemanticCache()
