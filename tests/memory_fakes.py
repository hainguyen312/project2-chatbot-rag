"""Bản giả in-memory cho MongoDB / OpenAI để test MemoryManager offline."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from types import SimpleNamespace

DIM = 64


def fake_embed(text: str) -> list[float]:
    """Embedding giả: túi âm tiết băm vào 64 chiều, chuẩn hoá → cosine có ý nghĩa thô."""
    v = [0.0] * DIM
    for tok in re.findall(r"\w+", (text or "").lower()):
        v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


# ── MongoDB ──────────────────────────────────────────────────────────────────
class _Cursor(list):
    def sort(self, key, direction=1):
        return _Cursor(sorted(self, key=lambda d: d.get(key) or "", reverse=direction < 0))

    def limit(self, n):
        return _Cursor(self[:n])


class FakeMongo:
    def __init__(self):
        self.docs: list[dict] = []
        self._oid = 0
        self.fail_find = False

    @staticmethod
    def _match(doc, flt):
        for k, cond in (flt or {}).items():
            v = doc.get(k)
            if isinstance(cond, dict):
                if "$ne" in cond and v == cond["$ne"]:
                    return False
                if "$in" in cond and v not in cond["$in"]:
                    return False
            elif v != cond:
                return False
        return True

    def insert_one(self, doc):
        self._oid += 1
        d = {"_id": self._oid, **copy.deepcopy(doc)}
        self.docs.append(d)
        return SimpleNamespace(inserted_id=d["_id"])

    def find_one(self, flt, proj=None):
        return next((copy.deepcopy(d) for d in self.docs if self._match(d, flt)), None)

    def find(self, flt=None, proj=None):
        if self.fail_find:
            raise RuntimeError("mongo down")
        drop = {k for k, v in (proj or {}).items() if v == 0}
        return _Cursor({k: v for k, v in copy.deepcopy(d).items() if k not in drop}
                       for d in self.docs if self._match(d, flt))

    def active(self):
        """Doc còn hiệu lực (tiện cho assert trong test)."""
        return [d for d in self.docs if not d.get("deleted")]

    def update_one(self, flt, upd, upsert=False):
        d = next((d for d in self.docs if self._match(d, flt)), None)
        if d is None:
            if not upsert:
                return SimpleNamespace(matched_count=0, modified_count=0)
            d = {"_id": None, **{k: v for k, v in flt.items() if not isinstance(v, dict)}}
            self.insert_one(d)
            d = self.docs[-1]
        d.update(copy.deepcopy(upd.get("$set", {})))
        for k, v in upd.get("$push", {}).items():
            d.setdefault(k, []).append(copy.deepcopy(v))
        return SimpleNamespace(matched_count=1, modified_count=1)

    def delete_one(self, flt):
        for i, d in enumerate(self.docs):
            if self._match(d, flt):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def delete_many(self, flt):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not self._match(d, flt)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    def create_index(self, *a, **k):
        pass


# ── OpenAI ───────────────────────────────────────────────────────────────────
class FakeOpenAI:
    """embeddings thật-giả (fake_embed); chat trả JSON theo hàng đợi cho từng schema."""

    def __init__(self):
        self.queues: dict[str, list] = {"memory_facts": [], "memory_decisions": []}
        self.chat_calls: list[dict] = []
        self.embed_calls: list = []
        self.embeddings = SimpleNamespace(create=self._embed)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

    def _embed(self, model, input):
        self.embed_calls.append(input)
        texts = input if isinstance(input, list) else [input]
        return SimpleNamespace(data=[SimpleNamespace(embedding=fake_embed(t)) for t in texts])

    def _chat(self, **kw):
        self.chat_calls.append(kw)
        name = kw.get("response_format", {}).get("json_schema", {}).get("name")
        q = self.queues.get(name) or []
        if not q:
            raise AssertionError(f"Không có response soạn sẵn cho schema {name}")
        payload = q.pop(0)
        if isinstance(payload, Exception):
            raise payload
        content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def make_manager():
    """MemoryManager nối với các bản giả (bỏ qua __init__ thật vốn kết nối mạng)."""
    from services.memory_manager import MemoryManager

    mm = MemoryManager.__new__(MemoryManager)
    mm.client = FakeOpenAI()
    mm.mongo_col = FakeMongo()
    mm.settings_col = FakeMongo()
    mm.mongo_uri = "mongodb://fake"
    mm.mongo_db_name = "test"
    return mm
