"""
Xác định người dùng ĐÃ XÁC THỰC cho các thao tác chạm tới bộ nhớ cá nhân.

Quy tắc:
- Có Firebase ID token hợp lệ → dùng uid trong token.
- Không có token → KHÔNG xác định được người dùng. Khách vãng lai bị chặn (SURVEY bước 5):
  id ẩn danh demo_… do trình duyệt tự sinh không còn được chấp nhận (8 ký tự hex — đoán được,
  và để khách gọi LLM tốn tiền không giới hạn).
- uid Firebase mà không kèm token → KHÔNG tin (trước đây ai biết uid người khác là đọc/xoá
  được memory của họ).

ANON_PREFIX còn dùng cho /auth/claim_anonymous: gắn dữ liệu ẩn danh cũ sang uid khi đăng nhập.
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


def require_uid(authorization: Optional[str], verify: Callable[[str], dict]) -> str:
    """uid từ token hợp lệ; không có → AuthError 401. Dùng cho mọi endpoint gọi LLM."""
    uid = token_uid(authorization, verify)
    if not uid:
        raise AuthError(401, "Cần đăng nhập")
    return uid


def resolve_user_id(body_user_id: Optional[str], authorization: Optional[str],
                    verify: Callable[[str], dict]) -> Optional[str]:
    """uid từ token; body_user_id (kể cả demo_…) không bao giờ được tin."""
    uid = token_uid(authorization, verify)
    if uid:
        return uid
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
    raise AuthError(401, "Cần đăng nhập để xem bộ nhớ của tài khoản này")


def require_role(role: str, authorization: Optional[str], verify: Callable[[str], dict],
                 get_role: Callable[[str], Optional[str]]) -> dict:
    """
    Chỉ cho qua nếu token Firebase hợp lệ, email đã xác minh và email có đúng vai trò.
    Trả về {uid, email, role}; raise AuthError 401 (chưa đăng nhập) / 403 (không đủ quyền).
    """
    if not (authorization and authorization.lower().startswith("bearer ")):
        raise AuthError(401, "Cần đăng nhập")
    try:
        claims = verify(authorization.split(None, 1)[1].strip())
    except Exception as e:
        print(f"[Auth] Token verify fail: {e}")
        raise AuthError(401, "Phiên đăng nhập không hợp lệ hoặc đã hết hạn")
    email = (claims.get("email") or "").strip().lower()
    if not email or not claims.get("email_verified"):
        raise AuthError(403, "Email của tài khoản chưa được xác minh")
    try:
        current = get_role(email)
    except Exception as e:
        print(f"[Auth] Không đọc được vai trò: {e}")
        raise AuthError(503, "Không kiểm tra được quyền, thử lại sau")
    if current != role:
        raise AuthError(403, "Tài khoản không có quyền truy cập")
    return {"uid": claims.get("uid"), "email": email, "role": current}
