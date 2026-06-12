import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  const body = await req.json();

  const backendUrl = process.env.RAG_BACKEND_URL ?? "http://localhost:8001";

  const res = await fetch(`${backendUrl}/rag/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    return NextResponse.json({ error: "Feedback save failed" }, { status: res.status });
  }

  const data = await res.json();
  return NextResponse.json(data);
}
