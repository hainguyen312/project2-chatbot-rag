import { NextRequest, NextResponse } from "next/server";

const BACKEND = process.env.RAG_BACKEND_URL ?? "http://localhost:8001";

/** Proxy tới rag_api /chat/title: LLM đặt tên ngắn cho đoạn chat từ câu hỏi đầu + câu trả lời. */
export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  try {
    const res = await fetch(`${BACKEND}/chat/title`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: String(body?.question ?? "").slice(0, 4000),
        answer: String(body?.answer ?? "").slice(0, 8000),
      }),
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
}
