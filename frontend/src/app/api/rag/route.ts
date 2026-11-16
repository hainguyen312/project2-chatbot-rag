import { NextResponse } from "next/server";
import { BACKEND_URL, backendHeaders, passThroughStatus } from "@/lib/backend";

export async function POST(req: Request) {
  try {
    const body = (await req.json().catch(() => ({}))) as {
      prompt?: string;
      history?: { role: "user" | "assistant"; content: string }[];
      query_mode?: "normal" | "situation";
    };

    if (!body.prompt?.trim()) {
      return NextResponse.json({ error: "Thiếu prompt" }, { status: 400 });
    }

    const response = await fetch(`${BACKEND_URL}/rag/chat`, {
      method: "POST",
      headers: backendHeaders(req, { "Content-Type": "application/json" }),
      body: JSON.stringify({
        prompt: body.prompt.trim(),
        history: body.history ?? [],
        query_mode: body.query_mode ?? "normal",
      }),
      signal: AbortSignal.timeout(120000),
    });

    const data = await response.json().catch(() => ({}));
    if (passThroughStatus(response.status)) {
      return NextResponse.json({ error: data?.detail || "Không được phép" }, { status: response.status });
    }
    if (!response.ok) {
      return NextResponse.json(
        {
          error: "Backend RAG lỗi",
          detail: data?.detail || data?.error || `HTTP ${response.status}`,
        },
        { status: 502 }
      );
    }

    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json(
      {
        error: "Không thể kết nối backend RAG",
        detail: String(error),
      },
      { status: 500 }
    );
  }
}

