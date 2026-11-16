/**
 * Gọi backend FastAPI từ route API Next.js (server). Một chỗ duy nhất quyết định header gửi kèm:
 * - Authorization: chuyển nguyên Firebase ID token của người dùng — backend tự verify, lấy uid.
 */
import type { NextRequest } from "next/server";

export const BACKEND_URL = process.env.RAG_BACKEND_URL ?? "http://localhost:8001";

/** Header cho request tới backend, kèm `extra` (vd Content-Type). */
export function backendHeaders(req: NextRequest | Request, extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  const auth = req.headers.get("authorization");
  if (auth) h["Authorization"] = auth;
  return h;
}

/** Mã lỗi backend nên trả nguyên cho trình duyệt (cần đăng nhập, hết lượt…). */
export function passThroughStatus(status: number): boolean {
  return status === 401 || status === 403 || status === 429;
}
