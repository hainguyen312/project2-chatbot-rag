import { NextRequest, NextResponse } from "next/server";
import { BACKEND_URL, backendHeaders } from "@/lib/backend";

export async function POST(req: NextRequest) {
  const formData = await req.formData();
  const file = formData.get("file") as File;
  if (!file) return NextResponse.json({ error: "No file" }, { status: 400 });

  const backendForm = new FormData();
  backendForm.append("file", file, "audio.webm");

  const res = await fetch(`${BACKEND_URL}/stt`, {
    method: "POST",
    headers: backendHeaders(req),
    body: backendForm,
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}