"""
Lịch sử rà soát văn bản (services/doc_review.py) — MongoDB.

- Collection `doc_reviews`: một document / lượt rà soát
    {_id, user_id, created_at, title, loai_van_ban, source{type,name,url}, counts,
     file{id,name,content_type,size} | None, text, result}
  `result` là nguyên kết quả trả cho người dùng (tóm tắt, ý chính, cảnh báo + căn cứ pháp luật).
- File gốc lưu ở GridFS bucket `doc_review_files`; chỉ truy cập qua document của chủ sở hữu.

Mọi hàm đọc/xoá đều lọc theo user_id → người này không xem được lịch sử của người khác.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

REVIEWS_COLLECTION = os.getenv("DOC_REVIEW_COLLECTION", "doc_reviews")
FILES_BUCKET = os.getenv("DOC_REVIEW_FILES_BUCKET", "doc_review_files")
LIST_LIMIT = 100


class FileStore(Protocol):
    def put(self, data: bytes, *, filename: str, content_type: str) -> Any: ...
    def get(self, file_id: Any) -> bytes: ...
    def delete(self, file_id: Any) -> None: ...


class GridFSStore:
    def __init__(self, db: Any, bucket: str = FILES_BUCKET):
        import gridfs

        self.fs = gridfs.GridFSBucket(db, bucket_name=bucket)

    def put(self, data: bytes, *, filename: str, content_type: str) -> Any:
        return self.fs.upload_from_stream(filename, data, metadata={"content_type": content_type})

    def get(self, file_id: Any) -> bytes:
        return self.fs.open_download_stream(file_id).read()

    def delete(self, file_id: Any) -> None:
        import gridfs

        try:
            self.fs.delete(file_id)
        except gridfs.errors.NoFile:
            pass


def _counts(result: dict[str, Any]) -> dict[str, int]:
    findings = result.get("canh_bao") or []
    return {
        "vi_pham": sum(1 for f in findings if f.get("danh_gia") == "vi_pham"),
        "canh_bao": len(findings),
        "thieu_sot": len(result.get("thieu_sot") or []),
    }


def _summary(doc: dict[str, Any]) -> dict[str, Any]:
    f = doc.get("file")
    return {
        "id": doc["_id"],
        "created_at": doc["created_at"].isoformat() if isinstance(doc.get("created_at"), datetime) else doc.get("created_at"),
        "title": doc.get("title") or "",
        "loai_van_ban": doc.get("loai_van_ban") or "",
        "source": doc.get("source") or {},
        "counts": doc.get("counts") or {},
        "file": {k: f[k] for k in ("name", "content_type", "size")} if f else None,
    }


class ReviewHistory:
    def __init__(self, collection: Any, files: Optional[FileStore]):
        self.col = collection
        self.files = files

    @classmethod
    def from_env(cls) -> Optional["ReviewHistory"]:
        uri = os.getenv("MONGODB_URI", "").strip()
        if not uri:
            return None
        from pymongo import ASCENDING, DESCENDING, MongoClient

        db = MongoClient(uri, serverSelectionTimeoutMS=10000)[os.getenv("MONGODB_DB") or "chatbot_rag"]
        col = db[REVIEWS_COLLECTION]
        try:
            col.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        except Exception as e:
            print(f"[ReviewHistory] Không tạo được index: {e}")
        return cls(col, GridFSStore(db))

    def save(self, user_id: str, result: dict[str, Any], *, text: str = "",
             file_bytes: Optional[bytes] = None, filename: str = "", content_type: str = "") -> str:
        rid = uuid.uuid4().hex
        file_meta = None
        if file_bytes and self.files is not None:
            if not content_type or content_type == "application/octet-stream":
                import mimetypes
                content_type = mimetypes.guess_type(filename or "")[0] or "application/octet-stream"
            fid = self.files.put(file_bytes, filename=filename or "van_ban", content_type=content_type)
            file_meta = {"id": fid, "name": filename, "content_type": content_type or "application/octet-stream",
                         "size": len(file_bytes)}
        source = dict(result.get("document") or {})
        title = result.get("tieu_de") or source.get("name") or source.get("url") or "Văn bản"
        doc = {
            "_id": rid,
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc),
            "title": str(title)[:200],
            "loai_van_ban": result.get("loai_van_ban") or "",
            "source": {k: source[k] for k in ("type", "name", "url") if source.get(k)},
            "counts": _counts(result),
            "file": file_meta,
            "text": text,
            "result": {k: v for k, v in result.items() if k not in ("type", "history_id")},
        }
        try:
            self.col.insert_one(doc)
        except Exception:
            if file_meta:
                self.files.delete(file_meta["id"])   # không để file mồ côi
            raise
        return rid

    def list(self, user_id: str, limit: int = LIST_LIMIT) -> list[dict[str, Any]]:
        cur = self.col.find({"user_id": user_id}, {"text": 0, "result": 0}).sort("created_at", -1).limit(limit)
        return [_summary(d) for d in cur]

    def get(self, user_id: str, rid: str) -> Optional[dict[str, Any]]:
        doc = self.col.find_one({"_id": rid, "user_id": user_id})
        if not doc:
            return None
        return {**_summary(doc), "text": doc.get("text") or "", "result": doc.get("result") or {}}

    def get_file(self, user_id: str, rid: str) -> Optional[tuple[bytes, str, str]]:
        doc = self.col.find_one({"_id": rid, "user_id": user_id}, {"file": 1})
        f = (doc or {}).get("file")
        if not f or self.files is None:
            return None
        return self.files.get(f["id"]), f.get("name") or "van_ban", f.get("content_type") or "application/octet-stream"

    def delete(self, user_id: str, rid: str) -> bool:
        doc = self.col.find_one_and_delete({"_id": rid, "user_id": user_id})
        if not doc:
            return False
        if doc.get("file") and self.files is not None:
            self.files.delete(doc["file"]["id"])
        return True

    def delete_all(self, user_id: str) -> int:
        n = 0
        for doc in self.col.find({"user_id": user_id}, {"_id": 1}):
            n += self.delete(user_id, doc["_id"])
        return n

    def retag(self, old_user_id: str, new_user_id: str) -> int:
        """Đăng nhập lần đầu: chuyển lịch sử của phiên ẩn danh sang tài khoản."""
        return self.col.update_many({"user_id": old_user_id}, {"$set": {"user_id": new_user_id}}).modified_count
