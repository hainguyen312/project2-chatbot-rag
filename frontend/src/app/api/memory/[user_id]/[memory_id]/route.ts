import { NextRequest, NextResponse } from "next/server";

import { BACKEND_URL as BACKEND, backendHeaders } from "@/lib/backend";

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ user_id: string; memory_id: string }> }
) {
  const { user_id, memory_id } = await params;
  const res = await fetch(
    `${BACKEND}/memory/${encodeURIComponent(user_id)}/${encodeURIComponent(memory_id)}`,
    { method: "DELETE", headers: backendHeaders(req) }
  );
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
