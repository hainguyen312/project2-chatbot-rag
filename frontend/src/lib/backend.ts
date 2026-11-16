/**
 * Gọi backend FastAPI từ route API Next.js (server). Một chỗ duy nhất quyết định header gửi kèm:
 * - Authorization: chuyển nguyên Firebase ID token của người dùng — backend tự verify, lấy uid.
 * - X-Client-IP: IP thật của người dùng cho rate limit theo IP. Cloudflare luôn ghi đè
 *   CF-Connecting-IP; frontend không mở cổng ra ngoài, chỉ nhận request qua tunnel.
 * - X-Internal-Secret: BACKEND_SHARED_SECRET — backend từ chối request thiếu/sai (chỉ Next.js
 *   gọi được backend; header X-Client-IP vì thế đáng tin). Biến server-only, không NEXT_PUBLIC.
 *
 * MỌI fetch tới backend phải đi qua internalHeaders()/backendHeaders() — tests/backend-usage.test.ts.
 */
import type { NextRequest } from "next/server";

export const BACKEND_URL = process.env.RAG_BACKEND_URL ?? "http://localhost:8001";
export const SECRET_HEADER = "X-Internal-Secret";

/** Chỉ header bí mật nội bộ (+ extra) — cho request không mang danh người dùng (vd verify token). */
export function internalHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  const secret = process.env.BACKEND_SHARED_SECRET;
  if (secret) h[SECRET_HEADER] = secret;
  return h;
}

/** IP người dùng: CF-Connecting-IP (Cloudflare) → phần tử đầu X-Forwarded-For → X-Real-IP. */
export function clientIp(req: NextRequest | Request): string {
  const cf = req.headers.get("cf-connecting-ip")?.trim();
  if (cf) return cf;
  const xff = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  if (xff) return xff;
  return req.headers.get("x-real-ip")?.trim() ?? "";
}

/** Header cho request tới backend, kèm `extra` (vd Content-Type). */
export function backendHeaders(req: NextRequest | Request, extra: Record<string, string> = {}): Record<string, string> {
  const h = internalHeaders(extra);
  const auth = req.headers.get("authorization");
  if (auth) h["Authorization"] = auth;
  const ip = clientIp(req);
  if (ip) h["X-Client-IP"] = ip.slice(0, 64);
  return h;
}

/** Mã lỗi backend nên trả nguyên cho trình duyệt (cần đăng nhập, hết lượt…). */
export function passThroughStatus(status: number): boolean {
  return status === 401 || status === 403 || status === 429;
}
