import { NextRequest } from "next/server";

const BACKEND = process.env.RAG_BACKEND_URL ?? "http://localhost:8001";

export async function POST(req: NextRequest) {
  const body = await req.text();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const auth = req.headers.get("authorization");
  if (auth) headers["Authorization"] = auth;

  const upstream = await fetch(`${BACKEND}/rag/stream`, {
    method: "POST",
    headers,
    body,
  });

  // Pipe stream thẳng về client
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "X-Accel-Buffering": "no",
    },
  });
}