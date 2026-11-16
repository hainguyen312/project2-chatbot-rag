import { NextRequest, NextResponse } from "next/server";

import { BACKEND_URL as BACKEND, backendHeaders } from "@/lib/backend";

function forwardHeaders(req: NextRequest): Record<string, string> {
  return backendHeaders(req, { "Content-Type": "application/json" });
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
