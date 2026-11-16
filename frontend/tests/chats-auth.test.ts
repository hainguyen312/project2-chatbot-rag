/**
 * /api/chats, /api/chats/[id]: uid lấy từ Firebase ID token đã verify (qua backend /auth/verify),
 * KHÔNG tin header x-user-id. Người A không đọc/sửa/xoá được chat của người B (SURVEY bước 5).
 *
 * fetch tới backend được giả lập: token "tok-A" → uid-A, "tok-B" → uid-B, còn lại 401.
 * lib/chats chạy chế độ bộ nhớ (MONGODB_URI rỗng — vitest.config.mts).
 */
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as list from "@/app/api/chats/route";
import * as one from "@/app/api/chats/[id]/route";
import { _clearAuthCache } from "@/lib/server-auth";

const TOKENS: Record<string, string> = { "tok-A": "uid-A", "tok-B": "uid-B" };
let verifyCalls = 0;
let backendDown = false;

function fakeFetch(url: string | URL | Request, init?: RequestInit): Promise<Response> {
  const u = String(url);
  if (!u.endsWith("/auth/verify")) throw new Error(`fetch không mong đợi: ${u}`);
  verifyCalls++;
  if (backendDown) return Promise.reject(new TypeError("fetch failed"));
  const { id_token } = JSON.parse(String(init?.body ?? "{}")) as { id_token?: string };
  const uid = id_token ? TOKENS[id_token] : undefined;
  return Promise.resolve(
    uid
      ? Response.json({ uid, email: `${uid}@example.invalid` })
      : Response.json({ detail: "Token không hợp lệ" }, { status: 401 }),
  );
}

beforeEach(() => {
  _clearAuthCache();
  verifyCalls = 0;
  backendDown = false;
  vi.stubGlobal("fetch", vi.fn(fakeFetch));
});
afterEach(() => vi.unstubAllGlobals());

function req(method: string, path: string, token?: string, body?: unknown, headers: Record<string, string> = {}) {
  const h: Record<string, string> = { ...headers };
  if (token) h.authorization = `Bearer ${token}`;
  if (body !== undefined) h["content-type"] = "application/json";
  return new NextRequest(`http://localhost${path}`, {
    method,
    headers: h,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}
const ctx = (id: string) => ({ params: Promise.resolve({ id }) });

async function createAs(token: string, title = "Hỏi về thừa kế") {
  const res = await list.POST(req("POST", "/api/chats", token, { title }));
  expect(res.status).toBe(201);
  return ((await res.json()) as { chat: { _id: string; user_id: string } }).chat;
}

describe("không có / sai token → 401", () => {
  it.each([
    ["GET list", () => list.GET(req("GET", "/api/chats"))],
    ["POST", () => list.POST(req("POST", "/api/chats", undefined, {}))],
    ["GET one", () => one.GET(req("GET", "/api/chats/x"), ctx("x"))],
    ["PATCH", () => one.PATCH(req("PATCH", "/api/chats/x", undefined, { title: "t" }), ctx("x"))],
    ["DELETE", () => one.DELETE(req("DELETE", "/api/chats/x"), ctx("x"))],
  ])("%s không token", async (_name, call) => {
    expect((await call()).status).toBe(401);
  });

  it("token sai → 401", async () => {
    expect((await list.GET(req("GET", "/api/chats", "tok-sai"))).status).toBe(401);
  });

  it("chỉ có x-user-id (cách cũ) → 401", async () => {
    const res = await list.GET(req("GET", "/api/chats", undefined, undefined, { "x-user-id": "uid-A" }));
    expect(res.status).toBe(401);
  });
});

describe("A không truy cập được chat của B", () => {
  it("chat gắn uid lấy từ token, không phải từ client", async () => {
    const chat = await createAs("tok-A");
    expect(chat.user_id).toBe("uid-A");
  });

  it("danh sách chỉ có chat của mình", async () => {
    const a = await createAs("tok-A");
    const b = await createAs("tok-B");
    const listA = (await (await list.GET(req("GET", "/api/chats", "tok-A"))).json()) as { chats: { _id: string }[] };
    const ids = listA.chats.map((c) => c._id);
    expect(ids).toContain(a._id);
    expect(ids).not.toContain(b._id);
  });

  it("B đọc / sửa / xoá chat của A → 404, chat của A còn nguyên", async () => {
    const a = await createAs("tok-A", "Bí mật của A");
    expect((await one.GET(req("GET", `/api/chats/${a._id}`, "tok-B"), ctx(a._id))).status).toBe(404);
    expect(
      (await one.PATCH(req("PATCH", `/api/chats/${a._id}`, "tok-B", { title: "bị sửa" }), ctx(a._id))).status,
    ).toBe(404);
    expect((await one.DELETE(req("DELETE", `/api/chats/${a._id}`, "tok-B"), ctx(a._id))).status).toBe(404);

    const res = await one.GET(req("GET", `/api/chats/${a._id}`, "tok-A"), ctx(a._id));
    expect(res.status).toBe(200);
    expect(((await res.json()) as { chat: { title: string } }).chat.title).toBe("Bí mật của A");
  });

  it("B giả header x-user-id của A vẫn bị chặn", async () => {
    const a = await createAs("tok-A");
    const spoof = { "x-user-id": "uid-A" };
    expect((await one.GET(req("GET", `/api/chats/${a._id}`, "tok-B", undefined, spoof), ctx(a._id))).status).toBe(404);
    expect((await one.DELETE(req("DELETE", `/api/chats/${a._id}`, "tok-B", undefined, spoof), ctx(a._id))).status).toBe(404);
  });

  it("A sửa và xoá được chat của chính mình", async () => {
    const a = await createAs("tok-A");
    const p = await one.PATCH(req("PATCH", `/api/chats/${a._id}`, "tok-A", { title: "Tên mới" }), ctx(a._id));
    expect(p.status).toBe(200);
    expect((await one.DELETE(req("DELETE", `/api/chats/${a._id}`, "tok-A"), ctx(a._id))).status).toBe(200);
    expect((await one.GET(req("GET", `/api/chats/${a._id}`, "tok-A"), ctx(a._id))).status).toBe(404);
  });
});

describe("server-auth", () => {
  it("backend xác thực không phản hồi → 503", async () => {
    backendDown = true;
    expect((await list.GET(req("GET", "/api/chats", "tok-A"))).status).toBe(503);
  });

  it("cache kết quả verify theo token (không gọi backend mỗi request)", async () => {
    // token có exp trong tương lai để được cache
    const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
    const jwtLike = `h.${payload}.s`;
    TOKENS[jwtLike] = "uid-A";
    await list.GET(req("GET", "/api/chats", jwtLike));
    await list.GET(req("GET", "/api/chats", jwtLike));
    expect(verifyCalls).toBe(1);
  });

  it("token đã hết hạn (exp quá khứ) không được cache", async () => {
    const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) - 10 })).toString("base64url");
    const jwtLike = `h.${payload}.s2`;
    TOKENS[jwtLike] = "uid-A";
    await list.GET(req("GET", "/api/chats", jwtLike));
    await list.GET(req("GET", "/api/chats", jwtLike));
    expect(verifyCalls).toBe(2);
  });
});
