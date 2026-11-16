import { NextRequest, NextResponse } from "next/server";
import { BACKEND_URL, backendHeaders } from "@/lib/backend";

export async function POST(req: NextRequest) {
  const body = await req.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/rag/stream`, {
      method: "POST",
      headers: backendHeaders(req, { "Content-Type": "application/json" }),
      body,
    });
  } catch {
    return NextResponse.json({ error: "Không kết nối được máy chủ" }, { status: 502 });
  }

  // Lỗi (401 cần đăng nhập, 429 hết lượt…) → trả JSON đúng mã, không giả làm luồng SSE.
  if (!upstream.ok || !upstream.body) {
    const data = (await upstream.json().catch(() => ({}))) as { detail?: unknown };
    const message = typeof data.detail === "string" ? data.detail : `Máy chủ trả lỗi ${upstream.status}`;
    return NextResponse.json({ error: message }, { status: upstream.status || 502 });
  }

  // Pipe stream thẳng về client
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "X-Accel-Buffering": "no",
    },
  });
}
