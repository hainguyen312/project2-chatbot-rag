import { NextRequest, NextResponse } from "next/server";
import { BACKEND_URL, backendHeaders } from "@/lib/backend";

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));

  const res = await fetch(`${BACKEND_URL}/rag/feedback`, {
    method: "POST",
    headers: backendHeaders(req, { "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    return NextResponse.json({ error: "Feedback save failed" }, { status: res.status });
  }

  const data = await res.json();
  return NextResponse.json(data);
}
