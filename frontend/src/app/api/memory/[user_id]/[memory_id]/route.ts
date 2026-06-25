import { NextRequest, NextResponse } from "next/server";

const BACKEND = process.env.RAG_BACKEND_URL ?? "http://localhost:8001";

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ user_id: string; memory_id: string }> }
) {
  const { user_id, memory_id } = await params;
  const auth = req.headers.get("authorization");
  const res = await fetch(
    `${BACKEND}/memory/${encodeURIComponent(user_id)}/${encodeURIComponent(memory_id)}`,
    { method: "DELETE", headers: auth ? { Authorization: auth } : {} }
  );
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
