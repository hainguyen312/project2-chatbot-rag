"""
BM25 lexical search trên Pháp Điển (bảng MySQL `pddieu`).

Vì sao cần:
  - Milvus collection hiện chỉ embed TÊN ĐIỀU → câu hỏi chứa thuật ngữ chính xác nằm
    trong nội dung ("thai sản", "án treo", "nồng độ cồn"...) dễ bị bỏ lỡ.
  - Milvus 2.4 chưa có BM25 built-in; MySQL FULLTEXT mặc định bỏ token < 3 ký tự
    (mất phần lớn âm tiết tiếng Việt: "án", "tù", "xe"...). Nên tự dựng BM25 in-process.

Tokenizer: lowercase + NFC → âm tiết (\\w+) bỏ stopword → thêm bigram âm tiết
(từ ghép tiếng Việt phần lớn là 2 âm tiết: "thai_sản", "người_lao", "lao_động").
Tên điều được lặp TITLE_BOOST lần để tăng trọng số.

Index lưu cache ở BM25_CACHE_DIR (mặc định .cache/bm25_phapdien), build lần đầu ~1–2 phút.
"""
from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = Path(os.getenv("BM25_CACHE_DIR", str(_ROOT / ".cache" / "bm25_phapdien")))
TITLE_BOOST = 3
BODY_MAX_CHARS = 6000  # điều quá dài (tới 50k ký tự) làm loãng BM25 & tốn RAM

_MAPC_PREFIX = re.compile(r"^\s*Điều\s+[\w.\-]+\.\s*", re.UNICODE)
_WORD = re.compile(r"\w+", re.UNICODE)

# Hư từ / đại từ / từ hỏi: bỏ khỏi unigram VÀ ngắt bigram.
FUNCTION_WORDS: frozenset[str] = frozenset(
    """
    và hoặc của các những là được bị có không cho với trong theo về để khi thì mà này đó
    một từ tại do nếu như đã sẽ đang cũng vẫn rằng nào gì sao bao nhiêu thế hay
    tôi mình bạn em anh chị ạ ơi nhé vậy hả à ư ấy kia nọ
    """.split()
)
# Âm tiết quá chung khi đứng một mình nhưng có nghĩa trong từ ghép ("điều_kiện",
# "quy_định", "làm_việc"): bỏ khỏi unigram, GIỮ trong bigram.
SOFT_STOPWORDS: frozenset[str] = frozenset(
    """
    điều khoản điểm luật nghị định thông tư quy phải cần muốn hỏi xin giúp làm
    ra vào lên xuống
    """.split()
)
STOPWORDS = FUNCTION_WORDS | SOFT_STOPWORDS


def clean_title(ten: str) -> str:
    """'Điều 1.1.LQ.1. Phạm vi điều chỉnh' -> 'Phạm vi điều chỉnh'."""
    t = (ten or "").strip()
    return _MAPC_PREFIX.sub("", t) or t


def tokenize(text: str, *, bigrams: bool = True) -> list[str]:
    text = unicodedata.normalize("NFC", (text or "").lower())
    raw = [w for w in _WORD.findall(text) if not w.isdigit()]
    toks = [w for w in raw if w not in STOPWORDS]
    if bigrams:
        toks += [
            f"{a}_{b}"
            for a, b in zip(raw, raw[1:])
            if a not in FUNCTION_WORDS and b not in FUNCTION_WORDS
            and not (a in SOFT_STOPWORDS and b in SOFT_STOPWORDS)
        ]
    return toks


def content_tokens(text: str) -> set[str]:
    """Âm tiết có nghĩa (không bigram) — dùng cho lexical coverage khi rerank."""
    return set(tokenize(text, bigrams=False))


def _mysql_rows() -> list[dict[str, Any]]:
    import pymysql

    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "123456789"),
        database=os.getenv("MYSQL_DATABASE", "law"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT mapc, ten, noidung FROM pddieu ORDER BY mapc")
            return list(cur.fetchall())
    finally:
        conn.close()


class BM25Index:
    def __init__(self, retriever: Any, mapcs: list[str]):
        self.retriever = retriever
        self.mapcs = mapcs

    @classmethod
    def build(cls) -> "BM25Index":
        import bm25s

        rows = _mysql_rows()
        mapcs, corpus = [], []
        for r in rows:
            title = clean_title(r.get("ten") or "")
            body = (r.get("noidung") or "")[:BODY_MAX_CHARS]
            mapcs.append(r["mapc"])
            corpus.append(tokenize(" ".join([title] * TITLE_BOOST) + " " + body))
        retriever = bm25s.BM25(k1=1.2, b=0.75)
        retriever.index(corpus, show_progress=False)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        retriever.save(str(CACHE_DIR))
        (CACHE_DIR / "mapcs.json").write_text(json.dumps(mapcs), encoding="utf-8")
        return cls(retriever, mapcs)

    @classmethod
    def load_or_build(cls) -> "BM25Index":
        import bm25s

        if (CACHE_DIR / "mapcs.json").exists():
            try:
                retriever = bm25s.BM25.load(str(CACHE_DIR))
                mapcs = json.loads((CACHE_DIR / "mapcs.json").read_text(encoding="utf-8"))
                return cls(retriever, mapcs)
            except Exception as e:  # cache hỏng → build lại
                print(f"[BM25] Load cache lỗi ({e}) → build lại")
        print("[BM25] Building index từ MySQL pddieu…")
        return cls.build()

    def search(self, query: str, top_k: int = 50) -> list[tuple[str, float]]:
        toks = tokenize(query)
        vocab = self.retriever.vocab_dict
        toks = [t for t in toks if t in vocab]
        if not toks:
            return []
        k = min(top_k, len(self.mapcs))
        docs, scores = self.retriever.retrieve([toks], k=k, show_progress=False)
        out = []
        for idx, s in zip(docs[0], scores[0]):
            if s > 0:
                out.append((self.mapcs[int(idx)], float(s)))
        return out


_INDEX: BM25Index | None = None
_LOCK = threading.Lock()
_FAILED = False


def get_bm25() -> BM25Index | None:
    """Singleton lazy; trả None nếu không dựng được (thiếu bm25s / MySQL) → hệ thống chạy vector-only."""
    global _INDEX, _FAILED
    if _INDEX is not None or _FAILED:
        return _INDEX
    with _LOCK:
        if _INDEX is None and not _FAILED:
            try:
                _INDEX = BM25Index.load_or_build()
                print(f"[BM25] Ready: {len(_INDEX.mapcs)} điều")
            except Exception as e:
                _FAILED = True
                print(f"[BM25] Không khởi tạo được, tắt lexical search: {e}")
    return _INDEX


if __name__ == "__main__":
    import sys
    import time

    t = time.time()
    idx = BM25Index.build() if "--rebuild" in sys.argv else BM25Index.load_or_build()
    print(f"index ready in {time.time() - t:.1f}s, {len(idx.mapcs)} docs")
    for q in sys.argv[1:]:
        if q.startswith("--"):
            continue
        print(f"\n> {q}  tokens={tokenize(q)}")
        for m, s in idx.search(q, 5):
            print(f"  {s:6.2f}  {m}")
