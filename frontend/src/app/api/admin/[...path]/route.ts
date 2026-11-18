import { NextRequest, NextResponse } from "next/server";

import { BACKEND_URL as BACKEND, backendHeaders } from "@/lib/backend";

/**
 * Proxy /api/admin/* → backend /admin/*. Quyền admin do BACKEND kiểm tra (token Firebase +
 * MongoDB user_roles); route này chỉ chuyển tiếp header Authorization và body.
 */
async function forward(req: NextRequest, path: string[], method: string) {
  const url = `${BACKEND}/admin/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const ct = req.headers.get("content-type");
  // giữ boundary của multipart khi upload PDF
  const headers = backendHeaders(req, ct ? { "Content-Type": ct } : {});
  const body = method === "GET" || method === "DELETE" ? undefined : await req.arrayBuffer();
  const res = await fetch(url, { method, headers, body, cache: "no-store" });
  const data = await res.json().catch(() => ({ detail: `Backend trả ${res.status}` }));
  return NextResponse.json(data, { status: res.status });
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, { params }: Ctx) {
  return forward(req, (await params).path, "GET");
}
export async function POST(req: NextRequest, { params }: Ctx) {
  return forward(req, (await params).path, "POST");
}
export async function PATCH(req: NextRequest, { params }: Ctx) {
  return forward(req, (await params).path, "PATCH");
}
export async function PUT(req: NextRequest, { params }: Ctx) {
  return forward(req, (await params).path, "PUT");
}
export async function DELETE(req: NextRequest, { params }: Ctx) {
  return forward(req, (await params).path, "DELETE");
}
