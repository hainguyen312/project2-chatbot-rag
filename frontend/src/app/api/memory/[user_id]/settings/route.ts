import { NextRequest, NextResponse } from "next/server";

const BACKEND = process.env.RAG_BACKEND_URL ?? "http://localhost:8001";

function forwardHeaders(req: NextRequest): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  const auth = req.headers.get("authorization");
  if (auth) h["Authorization"] = auth;
  return h;
}

/** Chế độ bộ nhớ của người dùng (no_sensitive | full | off). */
export async function GET(req: NextRequest, { params }: { params: Promise<{ user_id: string }> }) {
  const { user_id } = await params;
  const res = await fetch(`${BACKEND}/memory/${encodeURIComponent(user_id)}/settings`, {
    cache: "no-store",
    headers: forwardHeaders(req),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}

export async function PUT(req: NextRequest, { params }: { params: Promise<{ user_id: string }> }) {
  const { user_id } = await params;
  const res = await fetch(`${BACKEND}/memory/${encodeURIComponent(user_id)}/settings`, {
    method: "PUT",
    headers: forwardHeaders(req),
    body: await req.text(),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
