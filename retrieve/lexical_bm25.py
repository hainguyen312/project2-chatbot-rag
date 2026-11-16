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

Điều dài hơn BODY_MAX_CHARS (≈1.000 điều, tới 50k ký tự): tách theo KHOẢN thành nhiều tài liệu con
(mỗi tài liệu = tên điều + một nhóm khoản liền nhau ≤ BODY_MAX_CHARS), khi tìm thì gộp về mapc lấy điểm
cao nhất — trước đây chỉ index 6.000 ký tự đầu nên các khoản cuối điều dài không bao giờ tìm thấy.
Tắt bằng BM25_SPLIT_LONG=0.

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
BODY_MAX_CHARS = 6000  # độ dài tối đa một tài liệu BM25 (điều dài hơn → tách theo khoản)
SPLIT_LONG = os.getenv("BM25_SPLIT_LONG", "1").strip().lower() in ("1", "true", "yes")
INDEX_VERSION = 2       # đổi cách dựng corpus → tăng để cache cũ tự build lại

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


def article_docs(ten: str, noidung: str, *, split_long: bool = SPLIT_LONG,
                 max_chars: int = BODY_MAX_CHARS) -> list[str]:
    """Văn bản các tài liệu BM25 của một điều: 1 tài liệu, hoặc nhiều nếu điều dài và tách được khoản."""
    title = clean_title(ten or "")
    head = " ".join([title] * TITLE_BOOST)
    body = noidung or ""
    if not split_long or len(body) <= max_chars:
        return [head + " " + body[:max_chars]]
    from retrieve.legal_graph import split_khoan

    parts = [t for _, t in split_khoan(body)]
    if len(parts) < 2:
        return [head + " " + body[:max_chars]]
    chunks, cur = [], ""
    for t in parts:
        t = t[:max_chars]
        if cur and len(cur) + len(t) + 1 > max_chars:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n{t}" if cur else t
    if cur:
        chunks.append(cur)
    return [head + " " + c for c in chunks]


def _meta() -> dict:
    return {"version": INDEX_VERSION, "split_long": SPLIT_LONG, "body_max_chars": BODY_MAX_CHARS}


class BM25Index:
    def __init__(self, retriever: Any, mapcs: list[str]):
        self.retriever = retriever
        self.mapcs = mapcs

    @classmethod
    def build(cls) -> "BM25Index":
        import bm25s

        rows = _mysql_rows()
        mapcs, corpus = [], []  # mapcs[i] = điều của tài liệu i (điều dài có nhiều tài liệu)
        for r in rows:
            for doc in article_docs(r.get("ten") or "", r.get("noidung") or ""):
                mapcs.append(r["mapc"])
                corpus.append(tokenize(doc))
        retriever = bm25s.BM25(k1=1.2, b=0.75)
        retriever.index(corpus, show_progress=False)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        retriever.save(str(CACHE_DIR))
        (CACHE_DIR / "mapcs.json").write_text(json.dumps(mapcs), encoding="utf-8")
        (CACHE_DIR / "meta.json").write_text(json.dumps(_meta()), encoding="utf-8")
        return cls(retriever, mapcs)

    @classmethod
    def load_or_build(cls) -> "BM25Index":
        import bm25s

        meta_file = CACHE_DIR / "meta.json"
        stale = not meta_file.exists() or json.loads(meta_file.read_text(encoding="utf-8")) != _meta()
        if (CACHE_DIR / "mapcs.json").exists() and stale:
            print("[BM25] Cache khác phiên bản/cấu hình corpus → build lại")
        elif (CACHE_DIR / "mapcs.json").exists():
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
        # Điều dài có nhiều tài liệu con → lấy dư rồi gộp về mapc (giữ điểm cao nhất)
        k = min(top_k * 3, len(self.mapcs))
        docs, scores = self.retriever.retrieve([toks], k=k, show_progress=False)
        out, seen = [], set()
        for idx, s in zip(docs[0], scores[0]):
            m = self.mapcs[int(idx)]
            if s > 0 and m not in seen:
                seen.add(m)
                out.append((m, float(s)))
        return out[:top_k]


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
                print(f"[BM25] Ready: {len(set(_INDEX.mapcs))} điều, {len(_INDEX.mapcs)} tài liệu")
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


def rebuild_async() -> None:
    """Build lại toàn bộ index (bm25s không cập nhật từng phần) ở luồng nền rồi thay thế
    index đang dùng — request trong lúc build vẫn dùng index cũ."""
    def _run():
        global _INDEX, _FAILED
        try:
            idx = BM25Index.build()
            with _LOCK:
                _INDEX, _FAILED = idx, False
            print(f"[BM25] Rebuilt: {len(set(idx.mapcs))} điều, {len(idx.mapcs)} tài liệu")
        except Exception as e:
            print(f"[BM25] Rebuild lỗi, giữ index cũ: {e}")

    threading.Thread(target=_run, name="bm25-rebuild", daemon=True).start()
