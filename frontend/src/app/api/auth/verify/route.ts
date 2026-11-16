import { NextRequest, NextResponse } from "next/server";

import { BACKEND_URL as BACKEND, backendHeaders } from "@/lib/backend";

export async function POST(req: NextRequest) {
  const body = await req.text();
  const res = await fetch(`${BACKEND}/auth/verify`, {
    method: "POST",
    headers: backendHeaders(req, { "Content-Type": "application/json" }),
    body,
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
