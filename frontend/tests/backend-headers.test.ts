/** src/lib/backend.ts: header gửi tới backend (Authorization, X-Client-IP cho rate limit). */
import { afterEach, describe, expect, it, vi } from "vitest";

import { backendHeaders, clientIp, internalHeaders, passThroughStatus } from "@/lib/backend";

afterEach(() => vi.unstubAllEnvs());

const req = (headers: Record<string, string>) => new Request("http://localhost/api/x", { headers });

describe("clientIp", () => {
  it("ưu tiên CF-Connecting-IP (Cloudflare ghi đè, không giả được qua tunnel)", () => {
    expect(clientIp(req({ "cf-connecting-ip": "1.2.3.4", "x-forwarded-for": "9.9.9.9" }))).toBe("1.2.3.4");
  });
  it("không có CF → phần tử đầu X-Forwarded-For", () => {
    expect(clientIp(req({ "x-forwarded-for": "5.6.7.8, 10.0.0.1" }))).toBe("5.6.7.8");
  });
  it("X-Real-IP, rồi rỗng", () => {
    expect(clientIp(req({ "x-real-ip": "7.7.7.7" }))).toBe("7.7.7.7");
    expect(clientIp(req({}))).toBe("");
  });
});

describe("backendHeaders", () => {
  it("gửi Authorization + X-Client-IP + extra", () => {
    const h = backendHeaders(req({ authorization: "Bearer t", "cf-connecting-ip": "1.2.3.4" }), {
      "Content-Type": "application/json",
    });
    expect(h).toEqual({ "Content-Type": "application/json", Authorization: "Bearer t", "X-Client-IP": "1.2.3.4" });
  });
  it("không bịa header khi thiếu", () => {
    vi.stubEnv("BACKEND_SHARED_SECRET", "");
    expect(backendHeaders(req({}))).toEqual({});
  });
  it("cắt IP quá dài", () => {
    expect(backendHeaders(req({ "cf-connecting-ip": "a".repeat(200) }))["X-Client-IP"]).toHaveLength(64);
  });
});

it("passThroughStatus", () => {
  expect([401, 403, 429].every(passThroughStatus)).toBe(true);
  expect([200, 400, 500, 502].some(passThroughStatus)).toBe(false);
});

describe("shared secret Next.js → FastAPI", () => {
  it("backendHeaders + internalHeaders gửi X-Internal-Secret khi có BACKEND_SHARED_SECRET", () => {
    vi.stubEnv("BACKEND_SHARED_SECRET", "s3cret");
    expect(backendHeaders(req({ authorization: "Bearer t" }))["X-Internal-Secret"]).toBe("s3cret");
    expect(internalHeaders({ "Content-Type": "application/json" })).toEqual({
      "Content-Type": "application/json",
      "X-Internal-Secret": "s3cret",
    });
  });
  it("internalHeaders không chuyển header người dùng", () => {
    vi.stubEnv("BACKEND_SHARED_SECRET", "s3cret");
    expect(Object.keys(internalHeaders())).toEqual(["X-Internal-Secret"]);
  });
});
