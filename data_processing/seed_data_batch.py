from pymilvus import (
    connections,
    FieldSchema, CollectionSchema, DataType,
    Collection, utility
)
from openai import OpenAI
import json
import pandas as pd  # vẫn dùng cho error_rows -> csv
import pymysql

# ----------------- OpenAI EMBEDDING -----------------
client = OpenAI()

def embed_batch(texts):
    """Embed 1 batch text, lỗi thì ném exception ra ngoài."""
    resp = client.embeddings.create(
        model="text-embedding-3-small",
        input=texts
    )
    return [item.embedding for item in resp.data]

def embed_text(text: str):
    resp = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return resp.data[0].embedding

# ----------------- MILVUS -----------------
connections.connect("default", host="localhost", port="19530")
print("Connected to Milvus")

collection_name = "phapdien_simple_tendieu"
embedding_dim = 1536

fields = [
    FieldSchema(
        name="id",
        dtype=DataType.INT64,
        is_primary=True,
        auto_id=False
    ),
    FieldSchema(
        name="metadata",
        dtype=DataType.JSON,  # object chứa toàn bộ metadata
    ),
    FieldSchema(
        name="embedding",
        dtype=DataType.FLOAT_VECTOR,
        dim=embedding_dim
    ),
]
schema = CollectionSchema(fields, description="Phapdien: vector + metadata JSON")

# Nếu collection đã tồn tại thì drop để ingest mới
if utility.has_collection(collection_name):
    utility.drop_collection(collection_name)

collection = Collection(collection_name, schema)
collection.create_index(
    "embedding",
    {
        "index_type": "HNSW",
        "metric_type": "COSINE",
        "params": {"M": 16, "efConstruction": 200}
    },
)
collection.load()

print("Created collection:", collection_name)


# ----------------- DATA: đọc từ JSON -----------------

def load_data_from_mysql():
    conn = pymysql.connect(
        host="localhost",
        port=3306,
        user="root",
        password="123456789",
        database="law",
        charset="utf8mb4"
    )
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    
    cursor.execute("""
        SELECT 
            cd.ten    AS tenchude,
            dm.ten    AS tendemuc,
            ch.ten    AS tenchuong,
            d.ten     AS tendieu,
            d.noidung AS noidung
        FROM pddieu d
        LEFT JOIN pdchuong ch ON d.chuong_id = ch.mapc
        LEFT JOIN pddemuc  dm ON d.demuc_id  = dm.id
        LEFT JOIN pdchude  cd ON d.chude_id  = cd.id
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def select_batch(data, batch_size, offset):
    """Lấy 1 batch từ list data."""
    return data[offset: offset + batch_size]

def build_block_text(rec: dict) -> str:
    """
    Ghép block text để embed: (tenchude, tendemuc, tenchuong, tendieu, noidung).
    """
    parts = []

    # Các header nếu có
    tenchude = (rec.get("tenchude") or "").strip()
    tendemuc = (rec.get("tendemuc") or "").strip()
    tenchuong = (rec.get("tenchuong") or "").strip()
    tendieu = (rec.get("tendieu") or "").strip()

    if tenchude:
        parts.append(tenchude)
    if tendemuc:
        parts.append(tendemuc)
    if tenchuong:
        parts.append(tenchuong)
    if tendieu:
        parts.append(tendieu)

    # Nội dung chính (list -> string)
    nd = rec.get("noidung") or []
    if isinstance(nd, list):
        body = "\n".join(nd)
    else:
        body = str(nd)

    body = body.strip()
    if body:
        parts.append(body)

    # Ghép tất cả lại thành 1 đoạn text
    return "\n\n".join(parts)

# ----------------- INSERT BATCH -----------------
def insert_batch(records, global_offset, error_rows):
    """
    records: list[dict] mỗi dict có:
      - tenchude
      - tendemuc
      - tenchuong
      - tendieu
      - noidung: list[str]
    """
    try:
        # ❶ Dùng block (tendemuc + tenchuong + tendieu + noidung) để embed
        # texts = [build_block_text(rec) for rec in records]
        texts = [(rec.get("tendieu") or "").strip() for rec in records]


        print(f"Embedding {len(texts)} documents…")
        vectors = embed_batch(texts)

        # id tăng đều theo offset
        ids = list(range(global_offset + 1, global_offset + len(records) + 1))

        metadatas = []
        for rec in records:
            nd = rec.get("noidung") or []
            if isinstance(nd, list):
                body = "\n".join(nd)
            else:
                body = str(nd)

            body = body.strip()

            meta = {
                "tenchude": rec.get("tenchude") or None,
                "tendemuc": rec.get("tendemuc") or None,
                "tenchuong": rec.get("tenchuong") or None,
                "tendieu": rec.get("tendieu") or None,
                "noidung": body[:500],  # vẫn lưu thô để debug
            }
            metadatas.append(meta)

        collection.insert([
            ids,
            metadatas,
            vectors,
        ])

        print(f"Inserted batch [{global_offset} → {global_offset + len(records)}]")

    except Exception:
        print("Lỗi embed cả batch, chuyển sang embed từng bản ghi")
        ids = []
        metadatas = []
        vectors = []

        for i, rec in enumerate(records):
            # text = build_block_text(rec)
            text = (rec.get("tendieu") or "").strip()
            this_id = global_offset + i + 1

            try:
                vec = embed_text(text)
            except Exception as e:
                error_rows.append({
                    "id": this_id,
                    "tenchude": rec.get("tenchude"),
                    "tendieu": rec.get("tendieu"),
                    "len_text": len(text),
                    "error": str(e),
                })
                print(f"❌ Lỗi embed id={this_id}, tendieu={rec.get('tendieu')}, len={len(text)}: {e}")
                continue
            
            nd = rec.get("noidung") or []
            if isinstance(nd, list):
                body = "\n".join(nd)
            else:
                body = str(nd)

            body = body.strip()

            meta = {
                "tenchude": rec.get("tenchude") or None,
                "tendemuc": rec.get("tendemuc") or None,
                "tenchuong": rec.get("tenchuong") or None,
                "tendieu": rec.get("tendieu") or None,
                "noidung": body[:500],
            }

            ids.append(this_id)
            metadatas.append(meta)
            vectors.append(vec)

        if ids:
            collection.insert([ids, metadatas, vectors])
            print(f"✅ Inserted batch [{global_offset} → {global_offset + len(records)}], ok={len(ids)}, error={len(records)-len(ids)}")
        else:
            print(f"⚠ Batch [{global_offset} → {global_offset + len(records)}] không insert được bản ghi nào (tất cả lỗi)")

# ----------------- MAIN -----------------
def main():
    data = load_data_from_mysql()
    total = len(data)
    batch_size = 200

    num_batches = (total + batch_size - 1) // batch_size
    print("Total:", total)
    print("Total batches:", num_batches)

    error_rows = []

    offset = 0
    while offset < total:
        batch_records = select_batch(data, batch_size, offset)
        insert_batch(batch_records, offset, error_rows)
        offset += batch_size

    if error_rows:
        errors_df = pd.DataFrame(error_rows)
        errors_df.to_csv("embed_errors.csv", index=False, encoding="utf-8-sig")
        print(f"⚠ Đã ghi {len(error_rows)} bản ghi lỗi vào embed_errors.csv")
    else:
        print("✅ Không có bản ghi lỗi nào.")

    print("🎉 DONE - Ingest xong tất cả bản ghi.")

if __name__ == "__main__":
    main()
