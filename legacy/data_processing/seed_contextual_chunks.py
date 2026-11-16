"""
Seed collection "phapdien_ctx_chunks": embed header ngữ cảnh + nội dung điều, chia chunk.

Mỗi Điều -> 1..n chunk. Text embed của mỗi chunk:

    Chủ đề: ...
    Đề mục: ...
    Chương: ...
    Điều: <tên điều, đã bỏ mã pháp điển>
    <đoạn nội dung thứ i>

Vì sao phải chunk / giới hạn:
  - text-embedding-3-small nhận tối đa 8191 token / input  -> chunk body <= CHUNK_TOKENS.
  - Một request embeddings tối đa ~300k token, 2048 input   -> gom batch theo token budget.
  - Milvus VARCHAR max_length tính theo BYTE (<= 65535)      -> cắt text lưu theo byte UTF-8.
  - Milvus mặc định giới hạn 1 lần insert ~64MB (gRPC)       -> insert theo batch nhỏ.
Chỉ lưu mapc + chunk text (không lưu cả noidung) vào Milvus; khi trả lời thì lấy
full Điều từ MySQL theo mapc (giống pipeline tên điều).

Chạy:
    python data_processing/seed_contextual_chunks.py --dry-run     # chỉ thống kê chunk + ước tính token/chi phí
    python data_processing/seed_contextual_chunks.py               # tạo mới (drop nếu đã có)
    python data_processing/seed_contextual_chunks.py --resume      # bỏ qua các mapc đã có
"""

from __future__ import annotations

import argparse
import os
import re
import statistics
import time

import pymysql
import tiktoken

COLLECTION = "phapdien_ctx_chunks"
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536

CHUNK_TOKENS = 400          # token tối đa cho phần body của 1 chunk (chưa tính header)
OVERLAP_TOKENS = 60         # gối đầu giữa 2 chunk liên tiếp
HEADER_MAX_TOKENS = 200     # header dài bất thường thì cắt
REQUEST_TOKEN_BUDGET = 200_000   # < 300k giới hạn của OpenAI cho 1 request
REQUEST_MAX_INPUTS = 1000        # < 2048
INSERT_BATCH = 500

TEXT_MAX_BYTES = 16_000     # VARCHAR max_length (byte). ~460 token tiếng Việt << 16KB
TENDIEU_MAX_BYTES = 2_000
MAPC_MAX_BYTES = 128

ENC = tiktoken.get_encoding("cl100k_base")  # encoding của text-embedding-3-*

_MAPC_PREFIX = re.compile(r"^\s*Điều\s+[\w.\-]+\.\s*", re.UNICODE)
_SENT_SPLIT = re.compile(r"(?<=[.;:!?])\s+")


# ----------------------------------------------------------------- helpers
def ntok(text: str) -> int:
    return len(ENC.encode(text))


def truncate_tokens(text: str, max_tokens: int) -> str:
    ids = ENC.encode(text)
    return text if len(ids) <= max_tokens else ENC.decode(ids[:max_tokens])


def truncate_bytes(text: str, max_bytes: int) -> str:
    b = text.encode("utf-8")
    if len(b) <= max_bytes:
        return text
    return b[:max_bytes].decode("utf-8", errors="ignore")


def clean_title(tendieu: str) -> str:
    """'Điều 39.13.NĐ.102.21. Hành vi cản trở...' -> 'Hành vi cản trở...'"""
    t = (tendieu or "").strip()
    stripped = _MAPC_PREFIX.sub("", t)
    return stripped or t


def build_header(rec: dict) -> str:
    lines = []
    for label, key in (("Chủ đề", "tenchude"), ("Đề mục", "tendemuc"), ("Chương", "tenchuong")):
        v = (rec.get(key) or "").strip()
        if v:
            lines.append(f"{label}: {v}")
    lines.append(f"Điều: {clean_title(rec.get('tendieu') or '')}")
    return truncate_tokens("\n".join(lines), HEADER_MAX_TOKENS)


def _split_long_unit(unit: str, max_tokens: int) -> list[str]:
    """Đoạn quá dài -> tách theo câu; câu vẫn quá dài -> cắt cứng theo token."""
    if ntok(unit) <= max_tokens:
        return [unit]
    out: list[str] = []
    for sent in _SENT_SPLIT.split(unit):
        if ntok(sent) <= max_tokens:
            out.append(sent)
        else:
            ids = ENC.encode(sent)
            out.extend(ENC.decode(ids[i:i + max_tokens]) for i in range(0, len(ids), max_tokens))
    return out


def chunk_body(body: str, max_tokens: int = CHUNK_TOKENS, overlap: int = OVERLAP_TOKENS) -> list[str]:
    """
    Gom các đoạn (khoản/điểm, cách nhau bởi \\n) thành chunk <= max_tokens.
    Không cắt giữa đoạn nếu không cần; gối đầu bằng các đoạn cuối của chunk trước.
    """
    units: list[str] = []
    for para in (p.strip() for p in body.split("\n")):
        if para:
            units.extend(_split_long_unit(para, max_tokens))
    if not units:
        return [""]

    chunks: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for u in units:
        t = ntok(u) + 1
        if cur and cur_tok + t > max_tokens:
            chunks.append("\n".join(cur))
            # overlap: giữ lại các đoạn cuối có tổng <= overlap token
            keep: list[str] = []
            keep_tok = 0
            for prev in reversed(cur):
                pt = ntok(prev) + 1
                if keep_tok + pt > overlap:
                    break
                keep.insert(0, prev)
                keep_tok += pt
            cur, cur_tok = keep, keep_tok
        cur.append(u)
        cur_tok += t
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def build_chunks(rec: dict) -> list[dict]:
    header = build_header(rec)
    body = rec.get("noidung") or ""
    if isinstance(body, list):
        body = "\n".join(body)
    parts = chunk_body(str(body))
    out = []
    for i, part in enumerate(parts):
        text = f"{header}\n{part}".strip()
        out.append({
            "mapc": rec["mapc"],
            "chunk_idx": i,
            "n_chunks": len(parts),
            "tendieu": rec.get("tendieu") or "",
            "text": text,
            "tokens": ntok(text),
        })
    return out


# ----------------------------------------------------------------- data
def load_rows() -> list[dict]:
    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", 3306)),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "123456789"),
        database=os.getenv("MYSQL_DATABASE", "law"),
        charset="utf8mb4",
    )
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute("""
            SELECT d.mapc AS mapc, cd.ten AS tenchude, dm.ten AS tendemuc,
                   ch.ten AS tenchuong, d.ten AS tendieu, d.noidung AS noidung
            FROM pddieu d
            LEFT JOIN pdchuong ch ON d.chuong_id = ch.mapc
            LEFT JOIN pddemuc  dm ON d.demuc_id  = dm.id
            LEFT JOIN pdchude  cd ON d.chude_id  = cd.id
            ORDER BY d.mapc
        """)
        rows = cur.fetchall()
    conn.close()
    return rows


# ----------------------------------------------------------------- milvus
def setup_collection(resume: bool):
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility

    if resume and utility.has_collection(COLLECTION):
        col = Collection(COLLECTION)
        col.load()
        return col

    if utility.has_collection(COLLECTION):
        utility.drop_collection(COLLECTION)

    fields = [
        FieldSchema("id", DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema("mapc", DataType.VARCHAR, max_length=MAPC_MAX_BYTES),
        FieldSchema("chunk_idx", DataType.INT32),
        FieldSchema("n_chunks", DataType.INT32),
        FieldSchema("tendieu", DataType.VARCHAR, max_length=TENDIEU_MAX_BYTES),
        FieldSchema("text", DataType.VARCHAR, max_length=TEXT_MAX_BYTES),
        FieldSchema("embedding", DataType.FLOAT_VECTOR, dim=EMBED_DIM),
    ]
    col = Collection(COLLECTION, CollectionSchema(fields, description="Phapdien: header ngữ cảnh + chunk nội dung"))
    col.create_index("embedding", {"index_type": "HNSW", "metric_type": "COSINE",
                                   "params": {"M": 16, "efConstruction": 200}})
    col.create_index("mapc", {"index_type": "INVERTED"})
    col.load()
    return col


def existing_mapcs(col) -> set[str]:
    """mapc đã đủ chunk. Điều nào chỉ có một phần chunk (bị ngắt giữa chừng) thì xóa để làm lại."""
    counts: dict[str, int] = {}
    expected: dict[str, int] = {}
    it = col.query_iterator(batch_size=5000, expr="chunk_idx >= 0", output_fields=["mapc", "n_chunks"])
    while True:
        batch = it.next()
        if not batch:
            it.close()
            break
        for r in batch:
            counts[r["mapc"]] = counts.get(r["mapc"], 0) + 1
            expected[r["mapc"]] = r["n_chunks"]
    done = {m for m, c in counts.items() if c >= expected[m]}
    partial = [m for m in counts if m not in done]
    for i in range(0, len(partial), 500):
        quoted = ",".join('"' + m.replace('"', '\\"') + '"' for m in partial[i:i + 500])
        col.delete(f"mapc in [{quoted}]")
    if partial:
        print(f"Resume: xóa {len(partial)} điều bị ingest dở")
    return done


def embed_with_retry(client, texts: list[str], retries: int = 5) -> list[list[float]]:
    for attempt in range(retries):
        try:
            resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
            return [d.embedding for d in resp.data]
        except Exception as e:  # rate limit / mạng
            wait = 2 ** attempt
            print(f"  embed lỗi ({e.__class__.__name__}: {e}); thử lại sau {wait}s")
            time.sleep(wait)
    raise RuntimeError("Embed thất bại sau nhiều lần thử")


def token_batches(chunks: list[dict]):
    batch, tok = [], 0
    for c in chunks:
        if batch and (tok + c["tokens"] > REQUEST_TOKEN_BUDGET or len(batch) >= REQUEST_MAX_INPUTS):
            yield batch
            batch, tok = [], 0
        batch.append(c)
        tok += c["tokens"]
    if batch:
        yield batch


def insert_chunks(col, chunks: list[dict], vectors: list[list[float]]):
    for i in range(0, len(chunks), INSERT_BATCH):
        part = chunks[i:i + INSERT_BATCH]
        vecs = vectors[i:i + INSERT_BATCH]
        col.insert([
            [truncate_bytes(c["mapc"], MAPC_MAX_BYTES) for c in part],
            [c["chunk_idx"] for c in part],
            [c["n_chunks"] for c in part],
            [truncate_bytes(c["tendieu"], TENDIEU_MAX_BYTES) for c in part],
            [truncate_bytes(c["text"], TEXT_MAX_BYTES) for c in part],
            vecs,
        ])


# ----------------------------------------------------------------- main
def print_stats(rows: list[dict], chunks: list[dict]):
    per_article: dict[str, int] = {}
    for c in chunks:
        per_article[c["mapc"]] = c["n_chunks"]
    toks = [c["tokens"] for c in chunks]
    n_per = list(per_article.values())
    total_tok = sum(toks)
    print(f"Điều: {len(rows):,} | Chunk: {len(chunks):,} | chunk/điều TB={statistics.mean(n_per):.2f} max={max(n_per)}")
    print(f"Token/chunk: TB={statistics.mean(toks):.0f} p95={sorted(toks)[int(0.95 * len(toks))]} max={max(toks)}")
    print(f"Tổng token embed: {total_tok:,} (~${total_tok / 1e6 * 0.02:.2f} với {EMBED_MODEL})")
    bmax = max(len(c["text"].encode("utf-8")) for c in chunks)
    print(f"Text byte lớn nhất: {bmax:,} (giới hạn VARCHAR {TEXT_MAX_BYTES:,})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="Chỉ thống kê, không embed/insert")
    ap.add_argument("--limit", type=int, default=0, help="Chỉ lấy N điều đầu (để thử)")
    args = ap.parse_args()

    rows = load_rows()
    if args.limit:
        rows = rows[: args.limit]
    chunks = [c for r in rows if r.get("mapc") for c in build_chunks(r)]
    print_stats(rows, chunks)
    if args.dry_run:
        return

    from dotenv import load_dotenv
    from openai import OpenAI
    from pymilvus import connections

    load_dotenv()
    client = OpenAI()
    connections.connect("default", host=os.getenv("MILVUS_HOST", "localhost"),
                        port=os.getenv("MILVUS_PORT", "19530"))
    col = setup_collection(resume=args.resume)

    if args.resume:
        done = existing_mapcs(col)
        chunks = [c for c in chunks if c["mapc"] not in done]
        print(f"Resume: bỏ qua {len(done):,} điều đã có, còn {len(chunks):,} chunk")

    done_chunks = 0
    for batch in token_batches(chunks):
        vecs = embed_with_retry(client, [c["text"] for c in batch])
        insert_chunks(col, batch, vecs)
        done_chunks += len(batch)
        print(f"  {done_chunks:,}/{len(chunks):,} chunk")

    col.flush()
    print(f"Xong. Collection {COLLECTION}: {col.num_entities:,} entity")


if __name__ == "__main__":
    main()
