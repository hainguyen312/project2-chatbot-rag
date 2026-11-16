import { NextRequest } from "next/server";
import { BACKEND_URL as BACKEND, backendHeaders } from "@/lib/backend";

/**
 * Proxy multipart (file / url / text) → backend /review/analyze, trả nguyên luồng SSE.
 * Giữ Content-Type gốc để không mất boundary của multipart.
 */
export async function POST(req: NextRequest) {
  const ct = req.headers.get("content-type");
  const headers = backendHeaders(req, ct ? { "Content-Type": ct } : {});

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND}/review/analyze`, {
      method: "POST",
      headers,
      body: await req.arrayBuffer(),
      signal: AbortSignal.timeout(300_000),
    });
  } catch {
    const msg = JSON.stringify({ type: "error", message: "Không kết nối được máy chủ phân tích" });
    return new Response(`data: ${msg}\n\n`, { headers: { "Content-Type": "text/event-stream" } });
  }
  if (!upstream.ok || !upstream.body) {
    const detail = ((await upstream.json().catch(() => ({}))) as { detail?: unknown }).detail;
    const text =
      upstream.status === 401 ? "Cần đăng nhập để rà soát văn bản"
      : typeof detail === "string" ? detail
      : `Máy chủ trả lỗi ${upstream.status}`;
    const msg = JSON.stringify({ type: "error", message: text });
    return new Response(`data: ${msg}\n\n`, { headers: { "Content-Type": "text/event-stream" } });
  }
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "X-Accel-Buffering": "no",
    },
  });
}
