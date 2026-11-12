"""
Xác định người dùng ĐÃ XÁC THỰC cho các thao tác chạm tới bộ nhớ cá nhân.

Quy tắc:
- Có Firebase ID token hợp lệ → dùng uid trong token.
- Không có token → chỉ chấp nhận id ẩn danh (demo_…) do trình duyệt tự sinh.
- uid Firebase mà không kèm token → KHÔNG tin (trước đây ai biết uid người khác là đọc/xoá
  được memory của họ).
"""
from __future__ import annotations

from typing import Callable, Optional

ANON_PREFIX = "demo_"


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def token_uid(authorization: Optional[str], verify: Callable[[str], dict]) -> Optional[str]:
    """uid từ header 'Authorization: Bearer <token>', None nếu thiếu hoặc token sai."""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(None, 1)[1].strip()
        try:
            return verify(token).get("uid")
        except Exception as e:
            print(f"[Auth] Token verify fail: {e}")
    return None


def resolve_user_id(body_user_id: Optional[str], authorization: Optional[str],
                    verify: Callable[[str], dict]) -> Optional[str]:
    uid = token_uid(authorization, verify)
    if uid:
        return uid
    if body_user_id and body_user_id.startswith(ANON_PREFIX):
        return body_user_id
    if body_user_id:
        print(f"[Auth] Bỏ user_id chưa xác thực (không có token hợp lệ): {body_user_id[:6]}…")
    return None


def enforce_user_match(path_user_id: str, authorization: Optional[str],
                       verify: Callable[[str], dict]) -> str:
    """Cho phép thao tác memory của path_user_id hay không; raise AuthError nếu không."""
    uid = token_uid(authorization, verify)
    if uid:
        if uid != path_user_id:
            raise AuthError(403, "Không được phép thao tác memory của người dùng khác")
        return uid
    if path_user_id.startswith(ANON_PREFIX):
        return path_user_id
    raise AuthError(401, "Cần đăng nhập để xem bộ nhớ của tài khoản này")
