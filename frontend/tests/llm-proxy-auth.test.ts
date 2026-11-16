/**
 * Route gọi LLM (stream, rag, review, tts, stt, chats/title) chuyển nguyên Authorization tới
 * backend (backend bắt buộc token — khách vãng lai bị chặn) và trả đúng 401/429 cho trình duyệt.
 */
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as rag from "@/app/api/rag/route";
import * as review from "@/app/api/review/route";
import * as stream from "@/app/api/stream/route";
import * as stt from "@/app/api/stt/route";
import * as title from "@/app/api/chats/title/route";
import * as tts from "@/app/api/tts/route";

type Seen = { url: string; auth: string | null };
let seen: Seen[] = [];
let reply: () => Response;

beforeEach(() => {
  seen = [];
  reply = () => Response.json({ ok: true });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      seen.push({ url: String(url), auth: new Headers(init?.headers).get("authorization") });
      return reply();
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

const json = (path: string, body: unknown, token?: string) =>
  new NextRequest(`http://localhost${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });

function form(path: string, token?: string) {
  const fd = new FormData();
  fd.append("file", new Blob(["x"], { type: "audio/webm" }), "a.webm");
  fd.append("text", "hợp đồng");
  return new NextRequest(`http://localhost${path}`, {
    method: "POST",
    headers: token ? { authorization: `Bearer ${token}` } : {},
    body: fd,
  });
}

const CASES: [string, (t?: string) => Promise<Response>, string][] = [
  ["stream", (t) => stream.POST(json("/api/stream", { prompt: "hỏi" }, t)), "/rag/stream"],
  ["rag", (t) => rag.POST(json("/api/rag", { prompt: "hỏi" }, t)), "/rag/chat"],
  ["tts", (t) => tts.POST(json("/api/tts", { text: "đọc" }, t)), "/tts"],
  ["stt", (t) => stt.POST(form("/api/stt", t)), "/stt"],
  ["chats/title", (t) => title.POST(json("/api/chats/title", { question: "q", answer: "a" }, t)), "/chat/title"],
  ["review", (t) => review.POST(form("/api/review", t)), "/review/analyze"],
];

describe("chuyển tiếp Authorization tới backend", () => {
  it.each(CASES)("%s", async (_n, call, backendPath) => {
    await call("tok-A");
    expect(seen).toHaveLength(1);
    expect(seen[0].url.endsWith(backendPath)).toBe(true);
    expect(seen[0].auth).toBe("Bearer tok-A");
  });

  it.each(CASES)("%s không token → không bịa Authorization", async (_n, call) => {
    await call();
    expect(seen[0].auth).toBeNull();
  });
});

describe("backend từ chối → trình duyệt nhận đúng mã", () => {
  it.each(CASES.filter(([n]) => n !== "review"))("%s: 401 giữ nguyên", async (_n, call) => {
    reply = () => Response.json({ detail: "Cần đăng nhập" }, { status: 401 });
    expect((await call()).status).toBe(401);
  });

  it("stream: 429 trả JSON kèm thông điệp, không giả làm luồng SSE", async () => {
    reply = () => Response.json({ detail: "Hệ thống đã hết lượt hỏi hôm nay" }, { status: 429 });
    const res = await stream.POST(json("/api/stream", { prompt: "hỏi" }, "tok-A"));
    expect(res.status).toBe(429);
    expect(res.headers.get("content-type")).toContain("application/json");
    expect(((await res.json()) as { error: string }).error).toContain("hết lượt");
  });

  it("rag: 429 không bị đổi thành 502", async () => {
    reply = () => Response.json({ detail: "Hết lượt" }, { status: 429 });
    expect((await rag.POST(json("/api/rag", { prompt: "hỏi" }, "tok-A"))).status).toBe(429);
  });

  it("review: 401 → sự kiện SSE lỗi dễ hiểu", async () => {
    reply = () => Response.json({ detail: "Cần đăng nhập" }, { status: 401 });
    const text = await (await review.POST(form("/api/review"))).text();
    expect(text).toContain('"type":"error"');
    expect(text).toContain("Cần đăng nhập");
  });

  it("stream: thành công vẫn pipe luồng SSE", async () => {
    reply = () => new Response('data: {"type":"done"}\n\n', { headers: { "content-type": "text/event-stream" } });
    const res = await stream.POST(json("/api/stream", { prompt: "hỏi" }, "tok-A"));
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/event-stream");
    expect(await res.text()).toContain('"done"');
  });
});
