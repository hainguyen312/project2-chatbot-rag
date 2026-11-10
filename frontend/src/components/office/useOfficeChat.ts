"use client";

import { useCallback, useRef, useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { demoStream } from "./demoStream";
import { director, type Passage, type StreamEvent } from "./director";

export type OfficeMessage = {
  role: "user" | "assistant";
  content: string;
  passages?: Passage[];
  cache_hit?: boolean;
};

function getAnonUserId(): string | null {
  try {
    const key = "rag_user_id";
    let id = window.localStorage.getItem(key);
    if (!id) {
      id = `demo_${Math.random().toString(36).slice(2, 10)}`;
      window.localStorage.setItem(key, id);
    }
    return id;
  } catch {
    return null;
  }
}

/**
 * Gửi câu hỏi tới /api/stream (proxy SSE → rag_api) và chuyển từng event
 * cho director để các nhân vật diễn. Câu trả lời chỉ hiện khi đã nhận đủ.
 */
export function useOfficeChat() {
  const { user, getIdToken } = useAuth();
  const [messages, setMessages] = useState<OfficeMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (prompt: string, queryMode: "normal" | "situation", mock = true) => {
      const text = prompt.trim();
      if (!text || busy) return;
      const history = [...messages, { role: "user" as const, content: text }];
      setMessages(history);
      setBusy(true);
      setStatus("Khách hàng đang trình bày…");
      director.beginQuestion(text);

      const ctrl = new AbortController();
      abortRef.current = ctrl;
      let answer = "";
      let passages: Passage[] = [];
      let cacheHit = false;

      const handleEvt = (evt: StreamEvent): boolean => {
        director.handle(evt);
        if (evt.type === "status" && evt.text) setStatus(evt.text);
        else if (evt.type === "meta") {
          passages = evt.passages ?? [];
          cacheHit = !!evt.cache_hit;
          setStatus("Luật sư đang trình bày câu trả lời…");
        } else if (evt.type === "token") answer += evt.text ?? "";
        else if (evt.type === "error") {
          answer += `\n\n⚠️ ${evt.message ?? "Lỗi không xác định"}`;
          return true;
        } else if (evt.type === "done") return true;
        return false;
      };

      try {
        if (mock) {
          const turn = messages.filter((m) => m.role === "user").length;
          for await (const evt of demoStream(text, ctrl.signal, { turn })) if (handleEvt(evt)) break;
          return;
        }
        const token = await getIdToken();
        const headers: Record<string, string> = { "Content-Type": "application/json" };
        if (token) headers["Authorization"] = `Bearer ${token}`;
        const res = await fetch("/api/stream", {
          method: "POST",
          headers,
          signal: ctrl.signal,
          body: JSON.stringify({
            prompt: text,
            history: history.map(({ role, content }) => ({ role, content })),
            query_mode: queryMode,
            user_id: user?.uid ?? getAnonUserId(),
          }),
        });
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let finished = false;
        while (!finished) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            let evt: StreamEvent;
            try {
              evt = JSON.parse(line.slice(6));
            } catch {
              continue;
            }
            if (handleEvt(evt)) finished = true;
          }
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          answer += `\n\n⚠️ Không kết nối được backend: ${(e as Error).message}`;
          director.handle({ type: "error", message: (e as Error).message });
        }
      } finally {
        director.handle({ type: "done" });
        setMessages((m) => [
          ...m,
          { role: "assistant", content: answer.trim() || "Văn phòng chưa thể trả lời. Vui lòng thử lại.", passages, cache_hit: cacheHit },
        ]);
        setBusy(false);
        setStatus(null);
        abortRef.current = null;
      }
    },
    [busy, messages, getIdToken, user],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);
  const reset = useCallback(() => setMessages([]), []);

  return { messages, busy, status, send, stop, reset };
}
