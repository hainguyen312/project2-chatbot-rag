/**
 * Xác thực phía server cho route API Next.js: verify Firebase ID token (Authorization: Bearer …)
 * qua backend /auth/verify (firebase_admin.verify_id_token). KHÔNG tin header/body do client
 * gửi (x-user-id, user_id) — uid luôn lấy từ token đã verify.
 *
 * Cache kết quả verify ngắn hạn (theo token) để không gọi backend mỗi request; không vượt quá
 * hạn của token.
 */
import { NextRequest, NextResponse } from "next/server";
import { BACKEND_URL as BACKEND, internalHeaders } from "@/lib/backend";

const CACHE_TTL_MS = 5 * 60_000;
const CACHE_MAX = 1000;

export type AuthedUser = { uid: string; email?: string | null };

const cache = new Map<string, { user: AuthedUser; until: number }>();

function bearer(req: NextRequest): string | null {
  const h = req.headers.get("authorization") ?? "";
  const m = /^Bearer\s+(.+)$/i.exec(h.trim());
  return m ? m[1].trim() : null;
}

/** Hạn (ms epoch) đọc từ payload JWT — chỉ để giới hạn cache, không dùng để tin cậy. */
function tokenExpMs(token: string): number {
  try {
    const payload = JSON.parse(Buffer.from(token.split(".")[1] ?? "", "base64url").toString("utf8"));
    return typeof payload.exp === "number" ? payload.exp * 1000 : 0;
  } catch {
    return 0;
  }
}

function unauthorized(message = "Cần đăng nhập") {
  return NextResponse.json({ error: message }, { status: 401 });
}

/**
 * Trả AuthedUser nếu token hợp lệ, ngược lại NextResponse (401 / 503) để route trả về ngay:
 *
 *   const auth = await requireUser(req);
 *   if (auth instanceof NextResponse) return auth;
 */
export async function requireUser(req: NextRequest): Promise<AuthedUser | NextResponse> {
  const token = bearer(req);
  if (!token) return unauthorized();

  const now = Date.now();
  const hit = cache.get(token);
  if (hit && hit.until > now) return hit.user;
  if (hit) cache.delete(token);

  let res: Response;
  try {
    res = await fetch(`${BACKEND}/auth/verify`, {
      method: "POST",
      headers: internalHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ id_token: token }),
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    return NextResponse.json({ error: "Máy chủ xác thực không phản hồi" }, { status: 503 });
  }
  if (res.status === 401) return unauthorized("Phiên đăng nhập không hợp lệ hoặc đã hết hạn");
  if (!res.ok) {
    return NextResponse.json({ error: "Không xác thực được" }, { status: 503 });
  }
  const data = (await res.json().catch(() => ({}))) as { uid?: unknown; email?: unknown };
  if (typeof data.uid !== "string" || !data.uid) return unauthorized("Phiên đăng nhập không hợp lệ");

  const user: AuthedUser = { uid: data.uid, email: typeof data.email === "string" ? data.email : null };
  const exp = tokenExpMs(token);
  const until = Math.min(now + CACHE_TTL_MS, exp || now);
  if (until > now) {
    if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value as string);
    cache.set(token, { user, until });
  }
  return user;
}

/** Chỉ dùng trong test. */
export function _clearAuthCache() {
  cache.clear();
}
