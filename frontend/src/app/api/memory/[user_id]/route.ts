import { NextRequest, NextResponse } from "next/server";

const BACKEND = process.env.RAG_BACKEND_URL ?? "http://localhost:8001";

function forwardHeaders(req: NextRequest): Record<string, string> {
  const h: Record<string, string> = {};
  const auth = req.headers.get("authorization");
  if (auth) h["Authorization"] = auth;
  return h;
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ user_id: string }> }
) {
  const { user_id } = await params;
  const res = await fetch(`${BACKEND}/memory/${encodeURIComponent(user_id)}`, {
    cache: "no-store",
    headers: forwardHeaders(req),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ user_id: string }> }
) {
  const { user_id } = await params;
  const res = await fetch(`${BACKEND}/memory/${encodeURIComponent(user_id)}`, {
    method: "DELETE",
    headers: forwardHeaders(req),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
