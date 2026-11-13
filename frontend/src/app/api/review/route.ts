import { NextRequest } from "next/server";

const BACKEND = process.env.RAG_BACKEND_URL ?? "http://localhost:8001";

/**
 * Proxy multipart (file / url / text) → backend /review/analyze, trả nguyên luồng SSE.
 * Giữ Content-Type gốc để không mất boundary của multipart.
 */
export async function POST(req: NextRequest) {
  const headers: Record<string, string> = {};
  const ct = req.headers.get("content-type");
  if (ct) headers["Content-Type"] = ct;
  const auth = req.headers.get("authorization");
  if (auth) headers["Authorization"] = auth;

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
    const msg = JSON.stringify({ type: "error", message: `Máy chủ trả lỗi ${upstream.status}` });
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
