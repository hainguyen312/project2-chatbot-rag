"""
Kết nối dùng chung: OpenAI client, MySQL engine (SQLAlchemy) và Milvus collection tên điều.

rag_api, services/lawdata và app.py import `client`, `collection`, `engine` từ module này.
Class TwoStageRetriever (rerank đợt 1) đã chuyển sang legacy/retrieve/two_stage_search.py.
"""
from openai import OpenAI
from pymilvus import Collection, connections
from sqlalchemy import create_engine

# OpenAI client (embedding + chat)
client = OpenAI()

# MySQL engine (app.py — giao diện Streamlit cũ)
engine = create_engine("mysql+mysqlconnector://root:123456789@localhost:3306/law")

# Milvus
connections.connect("default", host="localhost", port="19530")
print("Connected to Milvus")

collection_name = "phapdien_simple_tendieu"
try:
    collection = Collection(collection_name)
    collection.load()
    print("Loaded collection:", collection_name)
except Exception as _col_err:
    print(f"[Milvus] Collection '{collection_name}' chưa tồn tại hoặc chưa load được: {_col_err}")
    print("[Milvus] Server vẫn khởi động — chức năng vector search sẽ không hoạt động cho đến khi collection được tạo.")
    collection = None  # type: ignore
