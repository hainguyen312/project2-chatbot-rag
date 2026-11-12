"""
Chuyển bộ nhớ dài hạn sang MỘT kho: MongoDB (embedding nằm trong document).

Với mỗi memory trong Mongo:
  - chưa có `mem_id`     → gán id mới (giữ nguyên id cho các lần chạy sau)
  - chưa có `embedding`  → chép vector có sẵn từ Milvus `user_memory` theo `milvus_id`
                           (không gọi OpenAI); thiếu vector trong Milvus thì embed lại
                           bằng OpenAI nếu dùng --embed-missing
Không xoá gì. Collection Milvus cũ chỉ bị xoá khi chạy --drop-milvus (sau khi đã kiểm tra).

    python scripts/migrate_memory_to_mongo.py --dry-run
    python scripts/migrate_memory_to_mongo.py
    python scripts/migrate_memory_to_mongo.py --drop-milvus     # dọn kho cũ khi đã yên tâm
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from pymongo import MongoClient  # noqa: E402

OLD_COLLECTION = "user_memory"


def milvus_vectors(ids: list[int]) -> dict[int, list[float]]:
    from pymilvus import Collection, connections, utility

    connections.connect("default", host=os.getenv("MILVUS_HOST", "localhost"), port=os.getenv("MILVUS_PORT", "19530"))
    if not utility.has_collection(OLD_COLLECTION) or not ids:
        return {}
    col = Collection(OLD_COLLECTION)
    col.load()
    out: dict[int, list[float]] = {}
    for i in range(0, len(ids), 200):
        part = ids[i:i + 200]
        for r in col.query(expr=f"id in {part}", output_fields=["id", "embedding"]):
            out[int(r["id"])] = [float(x) for x in r["embedding"]]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--embed-missing", action="store_true", help="embed lại bằng OpenAI khi Milvus không còn vector")
    ap.add_argument("--drop-milvus", action="store_true", help=f"xoá collection Milvus '{OLD_COLLECTION}' cũ")
    args = ap.parse_args()

    col = MongoClient(os.getenv("MONGODB_URI"))[os.getenv("MONGODB_DB") or "chatbot_rag"][
        os.getenv("MEMORY_MONGO_COLLECTION", "memories")]
    docs = list(col.find({}, {"history": 0}))
    need_id = [d for d in docs if not d.get("mem_id")]
    need_vec = [d for d in docs if not isinstance(d.get("embedding"), list) and not d.get("deleted")]
    vecs = milvus_vectors([int(d["milvus_id"]) for d in need_vec if d.get("milvus_id") is not None])
    missing = [d for d in need_vec if d.get("milvus_id") is None or int(d["milvus_id"]) not in vecs]

    print(f"Mongo: {len(docs)} memory · thiếu mem_id: {len(need_id)} · thiếu embedding (còn hiệu lực): "
          f"{len(need_vec)} → lấy được từ Milvus: {len(need_vec) - len(missing)}, không có: {len(missing)}")
    if args.dry_run:
        return

    filled = 0
    for d in docs:
        upd = {}
        if not d.get("mem_id"):
            upd["mem_id"] = uuid.uuid4().hex[:16]
        if d in need_vec and d.get("milvus_id") is not None and int(d["milvus_id"]) in vecs:
            upd["embedding"] = vecs[int(d["milvus_id"])]
            filled += 1
        if upd:
            col.update_one({"_id": d["_id"]}, {"$set": upd})

    if missing and args.embed_missing:
        from openai import OpenAI

        resp = OpenAI().embeddings.create(model="text-embedding-3-small", input=[d["fact"] for d in missing])
        for d, e in zip(missing, resp.data):
            col.update_one({"_id": d["_id"]}, {"$set": {"embedding": e.embedding}})
            filled += 1
        missing = []
    print(f"Đã gán mem_id: {len(need_id)} · đã điền embedding: {filled}"
          + (f" · ⚠ còn {len(missing)} memory chưa có embedding (chạy lại với --embed-missing)" if missing else ""))

    if args.drop_milvus:
        if missing:
            raise SystemExit("Không xoá Milvus: vẫn còn memory chưa có embedding trong Mongo.")
        from pymilvus import utility
        if utility.has_collection(OLD_COLLECTION):
            utility.drop_collection(OLD_COLLECTION)
            print(f"Đã xoá collection Milvus '{OLD_COLLECTION}'.")


if __name__ == "__main__":
    main()
