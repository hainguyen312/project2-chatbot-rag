"""
Khởi tạo Milvus collection `user_memory` cho hệ thống long-term memory.

Schema:
- id          INT64 primary key, auto_id=True
- user_id     VARCHAR(128)   — để filter theo người dùng (luôn dùng trong expr)
- mem_type    VARCHAR(32)    — core | episodic | semantic | procedural
- fact        VARCHAR(2000)  — nội dung fact tiếng Việt
- embedding   FLOAT_VECTOR dim=1536 (text-embedding-3-small)
- timestamp   VARCHAR(64)    — ISO8601

Index: HNSW, COSINE, M=16, efConstruction=200.

Chạy:
    python scripts/init_memory_collection.py [--drop]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

COLLECTION_NAME = "user_memory"
EMBEDDING_DIM = 1536


def build_schema() -> CollectionSchema:
    fields = [
        FieldSchema(name="id",        dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="user_id",   dtype=DataType.VARCHAR, max_length=128),
        FieldSchema(name="mem_type",  dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="fact",      dtype=DataType.VARCHAR, max_length=2000),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
        FieldSchema(name="timestamp", dtype=DataType.VARCHAR, max_length=64),
    ]
    return CollectionSchema(fields, description="User long-term memory (Mem0-style)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", action="store_true",
                        help="Xoá collection cũ trước khi tạo (cẩn thận: mất dữ liệu)")
    parser.add_argument("--host", default=os.getenv("MILVUS_HOST", "localhost"))
    parser.add_argument("--port", default=os.getenv("MILVUS_PORT", "19530"))
    args = parser.parse_args()

    print(f"[init] Connecting Milvus {args.host}:{args.port}…")
    connections.connect("default", host=args.host, port=args.port)

    exists = utility.has_collection(COLLECTION_NAME)
    if exists and args.drop:
        print(f"[init] Dropping existing collection '{COLLECTION_NAME}'…")
        utility.drop_collection(COLLECTION_NAME)
        exists = False

    if exists:
        col = Collection(COLLECTION_NAME)
        print(f"[init] Collection '{COLLECTION_NAME}' đã tồn tại "
              f"(num_entities={col.num_entities}). "
              "Dùng --drop để tạo lại.")
        col.load()
        return 0

    col = Collection(COLLECTION_NAME, build_schema())
    col.create_index(
        "embedding",
        {
            "index_type": "HNSW",
            "metric_type": "COSINE",
            "params": {"M": 16, "efConstruction": 200},
        },
    )
    col.load()
    print(f"[init] Đã tạo collection '{COLLECTION_NAME}' (dim={EMBEDDING_DIM}, HNSW/COSINE).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
