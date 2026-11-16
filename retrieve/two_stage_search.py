"""
Kết nối dùng chung: OpenAI client và Milvus collection tên điều.

rag_api và services/lawdata import `client`, `collection` từ module này.
(`engine` SQLAlchemy chỉ app.py dùng — đã gỡ cùng app.py, xem legacy/.)
Class TwoStageRetriever (rerank đợt 1) đã chuyển sang legacy/retrieve/two_stage_search.py.
"""
import os

from openai import OpenAI
from pymilvus import Collection, connections

# OpenAI client (embedding + chat)
client = OpenAI()

# Milvus
# MILVUS_HOST/PORT: trong docker compose là "milvus"; mặc định localhost cho máy dev.
MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")
connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)
print(f"Connected to Milvus {MILVUS_HOST}:{MILVUS_PORT}")

collection_name = "phapdien_simple_tendieu"
try:
    collection = Collection(collection_name)
    collection.load()
    print("Loaded collection:", collection_name)
except Exception as _col_err:
    print(f"[Milvus] Collection '{collection_name}' chưa tồn tại hoặc chưa load được: {_col_err}")
    print("[Milvus] Server vẫn khởi động — chức năng vector search sẽ không hoạt động cho đến khi collection được tạo.")
    collection = None  # type: ignore
