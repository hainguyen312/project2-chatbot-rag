import { NextRequest, NextResponse } from "next/server";

import { BACKEND_URL as BACKEND, backendHeaders } from "@/lib/backend";

/**
 * Proxy /api/review/history[/…] → backend /review/history[/…]. Quyền sở hữu do BACKEND
 * kiểm tra (token Firebase). File gốc trả nguyên bytes + header tải về.
 */
async function forward(req: NextRequest, path: string[] | undefined, method: "GET" | "DELETE") {
  const sub = (path ?? []).map(encodeURIComponent).join("/");
  const url = `${BACKEND}/review/history${sub ? `/${sub}` : ""}${req.nextUrl.search}`;
  const headers = backendHeaders(req);
  let res: Response;
  try {
    res = await fetch(url, { method, headers, cache: "no-store" });
  } catch {
    return NextResponse.json({ detail: "Không kết nối được máy chủ" }, { status: 502 });
  }
  const ct = res.headers.get("content-type") ?? "";
  if (res.ok && !ct.includes("application/json")) {
    const out = new Headers({ "Content-Type": ct, "Cache-Control": "private, no-store" });
    const cd = res.headers.get("content-disposition");
    if (cd) out.set("Content-Disposition", cd);
    return new Response(res.body, { status: res.status, headers: out });
  }
  const data = await res.json().catch(() => ({ detail: `Máy chủ trả ${res.status}` }));
  return NextResponse.json(data, { status: res.status });
}

type Ctx = { params: Promise<{ path?: string[] }> };

export async function GET(req: NextRequest, { params }: Ctx) {
  return forward(req, (await params).path, "GET");
}
export async function DELETE(req: NextRequest, { params }: Ctx) {
  return forward(req, (await params).path, "DELETE");
}
