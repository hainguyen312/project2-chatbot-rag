"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { chatActions, useChatStore, type Message } from "@/store/chat-store";
import { demoStream } from "./demoStream";
import { director, type Passage, type StreamEvent } from "./director";

export type OfficeMessage = {
  role: "user" | "assistant";
  content: string;
  passages?: Passage[];
  cache_hit?: boolean;
};

/** Cùng key/format với trang chat chính → dùng chung danh sách hội thoại. */
function getOrCreateAnonUserId(): string | null {
  try {
    const key = "rag_user_id";
    let id = window.localStorage.getItem(key);
    if (!id) {
      id = `demo_${crypto.randomUUID().slice(0, 8)}`;
      window.localStorage.setItem(key, id);
    }
    return id;
  } catch {
    return null;
  }
}

function autoTitle(messages: Message[], currentTitle: string) {
  if (!currentTitle.startsWith("Cuộc trò chuyện")) return currentTitle;
  const firstUser = messages.find((m) => m.role === "user")?.content?.trim();
  if (!firstUser) return currentTitle;
  return firstUser.length > 40 ? `${firstUser.slice(0, 40)}...` : firstUser;
}

/**
 * Gửi câu hỏi tới /api/stream (proxy SSE → rag_api) và chuyển từng event
 * cho director để các nhân vật diễn. Câu trả lời chỉ hiện khi đã nhận đủ.
 *
 * Live: dùng chung chat-store với trang chat chính — tự tạo hội thoại mới khi
 * chưa có, lưu tin nhắn vào /api/chats, gửi chat_id cho backend, và mở tiếp
 * được hội thoại cũ. Mock: chỉ giữ tin nhắn cục bộ, không lưu.
 */
export function useOfficeChat(mock: boolean, initialChatId: string | null = null) {
  const { user, getIdToken } = useAuth();
  const [anonId, setAnonId] = useState<string | null>(null);
  useEffect(() => setAnonId(getOrCreateAnonUserId()), []);
  const userId = user?.uid || anonId;

  const { chats, activeId, loading, initialized } = useChatStore((s) => s);
  const activeChat = useMemo(() => chats.find((c) => c._id === activeId) ?? null, [chats, activeId]);

  const [mockMessages, setMockMessages] = useState<OfficeMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const busyRef = useRef(false);

  useEffect(() => {
    if (userId) void chatActions.setUserId(userId);
  }, [userId]);

  // Mở đúng hội thoại truyền qua ?chat=<id> sau khi danh sách đã tải.
  const appliedInitial = useRef(false);
  useEffect(() => {
    if (appliedInitial.current || !initialized || !initialChatId) return;
    appliedInitial.current = true;
    if (chats.some((c) => c._id === initialChatId)) chatActions.setActiveChat(initialChatId);
  }, [initialized, initialChatId, chats]);

  const messages: OfficeMessage[] = mock ? mockMessages : ((activeChat?.messages ?? []) as OfficeMessage[]);

  const streamTurn = useCallback(
    async (
      text: string,
      history: OfficeMessage[],
      queryMode: "normal" | "situation",
      chatId: string | null,
      signal: AbortSignal,
    ) => {
      let answer = "";
      let passages: Passage[] = [];
      let cacheHit = false;
      let extra: Partial<Message> = {};

      const handleEvt = (evt: StreamEvent): boolean => {
        director.handle(evt);
        if (evt.type === "status" && evt.text) setStatus(evt.text);
        else if (evt.type === "meta") {
          passages = evt.passages ?? [];
          cacheHit = !!evt.cache_hit;
          const e = evt as StreamEvent & Partial<Message>;
          extra = {
            confidence_score: e.confidence_score ?? null,
            hallucination_warning: e.hallucination_warning ?? false,
            memories_used: e.memories_used ?? [],
          };
          setStatus("Luật sư đang trình bày câu trả lời…");
        } else if (evt.type === "token") answer += evt.text ?? "";
        else if (evt.type === "error") {
          answer += `\n\n⚠️ ${evt.message ?? "Lỗi không xác định"}`;
          return true;
        } else if (evt.type === "done") return true;
        return false;
      };

      try {
        if (!chatId) {
          const turn = history.filter((m) => m.role === "user").length - 1;
          for await (const evt of demoStream(text, signal, { turn })) if (handleEvt(evt)) break;
        } else {
          const token = await getIdToken();
          const headers: Record<string, string> = { "Content-Type": "application/json" };
          if (token) headers["Authorization"] = `Bearer ${token}`;
          const res = await fetch("/api/stream", {
            method: "POST",
            headers,
            signal,
            body: JSON.stringify({
              prompt: text,
              history: history.map(({ role, content }) => ({ role, content })),
              query_mode: queryMode,
              user_id: userId,
              chat_id: chatId,
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
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          answer += `\n\n⚠️ Không kết nối được backend: ${(e as Error).message}`;
          director.handle({ type: "error", message: (e as Error).message });
        }
      }
      director.handle({ type: "done" });
      return {
        role: "assistant" as const,
        content: answer.trim() || "Văn phòng chưa thể trả lời. Vui lòng thử lại.",
        passages,
        cache_hit: cacheHit,
        tts_url: null,
        ...extra,
      };
    },
    [getIdToken, userId],
  );

  const send = useCallback(
    async (prompt: string, queryMode: "normal" | "situation") => {
      const text = prompt.trim();
      if (!text || busyRef.current) return;
      busyRef.current = true;
      setBusy(true);
      setError(null);
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      try {
        if (mock) {
          const history = [...mockMessages, { role: "user" as const, content: text }];
          setMockMessages(history);
          setStatus("Khách hàng đang trình bày…");
          director.beginQuestion(text);
          const reply = await streamTurn(text, history, queryMode, null, ctrl.signal);
          setMockMessages((m) => [...m, reply]);
          return;
        }

        // Live: giống luồng chat chính — chưa có hội thoại thì tạo mới.
        let chat = activeChat;
        if (!chat) {
          try {
            chat = await chatActions.createChat();
          } catch (err) {
            setError(`Không tạo được cuộc trò chuyện: ${String(err)}`);
            return;
          }
        }
        const chatId = chat._id;
        const nextMessages: Message[] = [...chat.messages, { role: "user", content: text }];
        try {
          await chatActions.patchChat(chatId, { messages: nextMessages, title: autoTitle(nextMessages, chat.title) });
        } catch (err) {
          setError(`Không lưu được tin nhắn: ${String(err)}`);
          return;
        }

        setStatus("Khách hàng đang trình bày…");
        director.beginQuestion(text);
        const reply = await streamTurn(text, nextMessages, queryMode, chatId, ctrl.signal);
        try {
          await chatActions.patchChat(chatId, { messages: [...nextMessages, reply] });
        } catch (err) {
          setError(`Không lưu được câu trả lời: ${String(err)}`);
        }
      } finally {
        busyRef.current = false;
        setBusy(false);
        setStatus(null);
        abortRef.current = null;
      }
    },
    [mock, mockMessages, activeChat, streamTurn],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  /** Vụ việc mới: live → bỏ chọn, câu hỏi kế tiếp sẽ tạo hội thoại mới. */
  const reset = useCallback(() => {
    if (busyRef.current) return;
    if (mock) setMockMessages([]);
    else chatActions.setActiveChat(null);
  }, [mock]);

  const openChat = useCallback((id: string) => {
    if (busyRef.current) return;
    chatActions.setActiveChat(id);
  }, []);

  return {
    messages,
    busy,
    status,
    error,
    send,
    stop,
    reset,
    chats,
    activeChat,
    chatsLoading: loading,
    openChat,
  };
}
