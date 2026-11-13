"""
Vai trò người dùng (admin, …) lưu trong MongoDB, collection `user_roles`.

Một document / email:  {_id: "<email chữ thường>", role: "admin", updated_at: ...}
Khoá theo email (không theo uid) để cấp quyền được TRƯỚC khi người đó đăng nhập lần đầu.
Backend chỉ tin email khi Firebase báo email_verified = true (xem auth_guard.require_role).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

ROLES_COLLECTION = os.getenv("ROLES_COLLECTION", "user_roles")
VALID_ROLES = ("admin",)


def norm_email(email: str) -> str:
    return (email or "").strip().lower()


class RoleStore:
    def __init__(self, collection: Any):
        self.col = collection

    @classmethod
    def from_env(cls) -> Optional["RoleStore"]:
        uri = os.getenv("MONGODB_URI", "").strip()
        if not uri:
            return None
        from pymongo import MongoClient

        db = MongoClient(uri, serverSelectionTimeoutMS=10000)[os.getenv("MONGODB_DB") or "chatbot_rag"]
        return cls(db[ROLES_COLLECTION])

    def get_role(self, email: str) -> Optional[str]:
        doc = self.col.find_one({"_id": norm_email(email)})
        return doc.get("role") if doc else None

    def set_role(self, email: str, role: Optional[str]) -> None:
        """role=None → thu hồi."""
        key = norm_email(email)
        if not key or "@" not in key:
            raise ValueError("email không hợp lệ")
        if role is None:
            self.col.delete_one({"_id": key})
            return
        if role not in VALID_ROLES:
            raise ValueError(f"role phải là một trong {VALID_ROLES}")
        self.col.update_one(
            {"_id": key},
            {"$set": {"role": role, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

    def list_roles(self) -> list[dict]:
        return [{"email": d["_id"], "role": d.get("role")} for d in self.col.find({})]
