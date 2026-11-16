/**
 * Quét mã nguồn: mọi file gọi backend phải lấy URL + header từ @/lib/backend (shared secret,
 * Authorization, X-Client-IP) — không tự đọc RAG_BACKEND_URL, không tự dựng header.
 * Thêm route proxy mới mà quên → test đỏ.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..", "src");

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : /\.(ts|tsx)$/.test(n) ? [p] : [];
  });
}

const files = walk(SRC).map((p) => ({ path: relative(SRC, p), text: readFileSync(p, "utf8") }));
const LIB = "lib/backend.ts";

describe("gọi backend chỉ qua @/lib/backend", () => {
  it("chỉ lib/backend.ts đọc RAG_BACKEND_URL", () => {
    expect(files.filter((f) => f.text.includes("RAG_BACKEND_URL")).map((f) => f.path)).toEqual([LIB]);
  });

  it("chỉ lib/backend.ts đọc BACKEND_SHARED_SECRET", () => {
    expect(files.filter((f) => f.text.includes("BACKEND_SHARED_SECRET")).map((f) => f.path)).toEqual([LIB]);
  });

  // File có fetch( và dùng URL backend (kể cả dựng URL vào biến trước: fetch(url, …))
  const callers = files.filter(
    (f) => f.path !== LIB && /\bfetch\(/.test(f.text) && /\$\{(BACKEND|BACKEND_URL)\}/.test(f.text),
  );

  it("có route gọi backend (test không rỗng)", () => {
    expect(callers.length).toBeGreaterThanOrEqual(15);
  });

  it.each(callers.map((f) => [f.path, f] as const))("%s dùng backendHeaders/internalHeaders", (_p, f) => {
    expect(f.text).toMatch(/from "@\/lib\/backend"/);
    expect(f.text).toMatch(/\b(backendHeaders|internalHeaders)\(/);
  });

  it("không file client nào ('use client') import lib/backend (secret chỉ ở server)", () => {
    const leaks = files.filter((f) => /^["']use client["']/m.test(f.text) && f.text.includes("@/lib/backend"));
    expect(leaks.map((f) => f.path)).toEqual([]);
  });
});
