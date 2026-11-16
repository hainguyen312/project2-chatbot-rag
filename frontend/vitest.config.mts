import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    // Không dùng Mongo/backend thật: lib/chats chạy chế độ bộ nhớ, fetch được giả lập trong test.
    env: { MONGODB_URI: "", RAG_BACKEND_URL: "http://backend.test" },
  },
});
