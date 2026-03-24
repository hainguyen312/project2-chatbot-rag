"""
Seed Milvus collection `phapdien_tendieu` từ MySQL.
Dùng cho luồng chính: app.py -> two_stage_search -> MySQL + Milvus.

Schema Milvus: id (INT64, primary), metadata (JSON), embedding (FLOAT_VECTOR 1536)
- id: thứ tự hàng (1-based) khớp với ORDER BY trong query MySQL
- embedding: vector của cột "ten" (tên điều)
"""
import os
from pymilvus import (
    connections,
    FieldSchema, CollectionSchema, DataType,
    Collection, utility,
)
from openai import OpenAI
import pandas as pd
from sqlalchemy import create_engine

# ----------------- CONFIG -----------------
MYSQL_URL = os.getenv("MYSQL_URL", "mysql+mysqlconnector://root:123456789@localhost:3306/law")
MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = int(os.getenv("MILVUS_PORT", "19530"))
COLLECTION_NAME = "phapdien_tendieu"
EMBEDDING_DIM = 1536
BATCH_SIZE = 100

# Thứ tự SELECT phải GIỐNG HỆT trong retrieve/two_stage_search._select_noidung
MYSQL_ORDER = "cd.stt, dm.stt, COALESCE(ch.stt, 0), p.stt, p.mapc"

# ----------------- OPENAI -----------------
client = OpenAI()


def embed_batch(texts):
    """Embed batch texts."""
    if not texts:
        return []
    resp = client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    return [item.embedding for item in resp.data]


# ----------------- MYSQL -----------------
engine = create_engine(MYSQL_URL)


def fetch_all_dieus():
    """Lấy toàn bộ điều từ MySQL với thứ tự cố định (dùng cho cả seed và retrieve)."""
    query = f"""
        SELECT 
            p.*,
            cd.ten AS ten_chude,
            dm.ten AS ten_demuc,
            ch.ten AS ten_chuong
        FROM pddieu p
        LEFT JOIN pdchude cd ON cd.id = p.chude_id
        LEFT JOIN pddemuc dm ON dm.id = p.demuc_id
        LEFT JOIN pdchuong ch ON ch.mapc = p.chuong_id
        ORDER BY {MYSQL_ORDER}
    """
    return pd.read_sql(query, engine).fillna("")


# ----------------- MILVUS -----------------
def ensure_collection():
    """Tạo hoặc thay mới collection phapdien_tendieu."""
    connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)
    print("Connected to Milvus")

    if utility.has_collection(COLLECTION_NAME):
        utility.drop_collection(COLLECTION_NAME)
        print(f"Dropped existing collection: {COLLECTION_NAME}")

    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=False),
        FieldSchema(name="metadata", dtype=DataType.JSON),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
    ]
    schema = CollectionSchema(fields, description="Phapdien tendieu embeddings")
    coll = Collection(COLLECTION_NAME, schema)
    coll.create_index(
        "embedding",
        {
            "index_type": "HNSW",
            "metric_type": "COSINE",
            "params": {"M": 16, "efConstruction": 200},
        },
    )
    coll.load()
    print(f"Created collection: {COLLECTION_NAME}")
    return coll


def main():
    print("=== Seed Milvus từ MySQL ===")
    df = fetch_all_dieus()
    total = len(df)
    print(f"Tổng số điều từ MySQL: {total}")

    if total == 0:
        print("Không có dữ liệu trong MySQL. Chạy law-crawler/main.py trước.")
        return

    coll = ensure_collection()

    # Embed theo batch và insert
    offset = 0
    inserted = 0
    while offset < total:
        batch = df.iloc[offset : offset + BATCH_SIZE]
        texts = batch["ten"].astype(str).fillna("").tolist()

        print(f"Embedding batch [{offset + 1} - {offset + len(batch)}]...")
        vectors = embed_batch(texts)

        ids = list(range(offset + 1, offset + len(batch) + 1))
        metadatas = [
            {
                "ten": row.get("ten", "") or "",
                "ten_chude": row.get("ten_chude", "") or "",
                "ten_demuc": row.get("ten_demuc", "") or "",
                "ten_chuong": row.get("ten_chuong", "") or "",
                "vbqppl": row.get("vbqppl", "") or "",
            }
            for _, row in batch.iterrows()
        ]

        coll.insert([ids, metadatas, vectors])
        inserted += len(ids)
        offset += BATCH_SIZE
        print(f"  Inserted {len(ids)} records (total: {inserted})")

    coll.flush()
    print(f"Done. Tổng {inserted} bản ghi đã đưa vào Milvus.")


if __name__ == "__main__":
    main()
