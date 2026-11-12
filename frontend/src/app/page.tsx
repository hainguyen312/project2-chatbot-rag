"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Image from "next/image";
import { chatActions, Chat, Message, useChatStore, Passage, MemoryUsed } from "@/store/chat-store";
import {
  Search, MoreHorizontal, Pin, Trash2, Pencil,
  BookOpen, Menu, X, PanelLeftClose, PanelLeftOpen, SquarePen,
  ArrowUp, Building2, Mic, Moon, Square, Sun,
} from "lucide-react";
import { useTTS } from "@/hooks/useTTS";
import { useSTT } from "@/hooks/useSTT";
import { MessageRow, BotBubble } from "@/components/chat/MessageRow";
import { PassagePanel, PassageFullModal } from "@/components/chat/PassagePanel";
import { LiveWaveform } from "@/components/chat/LiveWaveform";
import { VoiceMessage } from "@/components/chat/VoiceMessage";
import { MemoryPanelButton } from "@/components/chat/MemoryPanel";
import { AuthButton } from "@/components/auth/AuthButton";
import { useAuth } from "@/hooks/useAuth";
import { useThemeMode } from "@/hooks/useThemeMode";

/** Câu hỏi mẫu ở màn hình trống — câu thật, mỗi câu một lĩnh vực người dân hay hỏi. */
const SUGGESTIONS = [
  { topic: "Lao động", q: "Lao động nữ đang mang thai có bị sa thải không?" },
  { topic: "Hôn nhân và gia đình", q: "Điều kiện để được kết hôn là gì?" },
  { topic: "Dân sự và hình sự", q: "Vay tiền không trả thì có bị đi tù không?" },
  { topic: "Giao thông", q: "Đi xe đạp điện không đội mũ bảo hiểm bị phạt bao nhiêu?" },
];
const SIDEBAR_KEY = "vnlaw-sidebar";
const SIDEBAR_W = 272;
const RAIL_W = 60;

function SidebarIconButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button" onClick={onClick} title={label} aria-label={label}
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
      style={{ color: "var(--text-secondary)" }}
    >
      {children}
    </button>
  );
}

if (typeof CanvasRenderingContext2D !== "undefined" &&
  !CanvasRenderingContext2D.prototype.roundRect) {
  CanvasRenderingContext2D.prototype.roundRect = function(
    x: number, y: number, w: number, h: number, r: number
  ) {
    this.beginPath();
    this.moveTo(x + r, y);
    this.arcTo(x + w, y, x + w, y + h, r);
    this.arcTo(x + w, y + h, x, y + h, r);
    this.arcTo(x, y + h, x, y, r);
    this.arcTo(x, y, x + w, y, r);
    this.closePath();
  };
}

/** Lấy hoặc tạo demo user_id lưu trong localStorage (sau này thay bằng auth thực). */
function getOrCreateUserId(): string {
  if (typeof window === "undefined") return "guest";
  const key = "rag_user_id";
  let id = window.localStorage.getItem(key);
  if (!id) {
    id = `demo_${crypto.randomUUID().slice(0, 8)}`;
    window.localStorage.setItem(key, id);
  }
  return id;
}

function autoTitle(messages: Message[], currentTitle: string) {
  if (!currentTitle.startsWith("Cuộc trò chuyện")) return currentTitle;
  const firstUser = messages.find((m) => m.role === "user")?.content?.trim();
  if (!firstUser) return currentTitle;
  return firstUser.length > 40 ? `${firstUser.slice(0, 40)}...` : firstUser;
}

function sleep(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}

/** Tốc độ gõ dần theo độ dài: câu ngắn chậm hơn, câu dài nhanh hơn (~1.5–8s). */
function getRevealTiming(textLength: number): { charStep: number; delayMs: number } {
  const len = textLength;
  if (len === 0) return { charStep: 1, delayMs: 0 };

  const MIN_TOTAL_MS = 1500;
  const MAX_TOTAL_MS = 8000;
  const targetMs = Math.min(MAX_TOTAL_MS, Math.max(MIN_TOTAL_MS, len * 8));

  let delayMs: number;
  if (len < 120) delayMs = 6;
  else if (len < 350) delayMs = 5;
  else if (len < 800) delayMs = 3;
  else delayMs = 2;

  delayMs = Math.min(14, Math.max(5, delayMs));
  const charStep = Math.max(1, Math.ceil(len / Math.ceil(targetMs / delayMs)));

  return { charStep, delayMs };
}

/**
 * Hiển thị dần theo thời gian thực — không bị kẹt khi chuyển tab trình duyệt.
 * Tab ẩn: hiện full text ngay.
 */
async function revealAnswerProgressively(
  text: string,
  setShown: (s: string) => void,
  opts?: { charStep?: number; delayMs?: number }
): Promise<void> {
  const t = text || "";
  if (!t) {
    setShown("");
    return;
  }
  if (typeof document !== "undefined" && document.hidden) {
    setShown(t);
    return;
  }

  const auto = getRevealTiming(t.length);
  const charStep = Math.max(1, opts?.charStep ?? auto.charStep);
  const delayMs = Math.max(0, opts?.delayMs ?? auto.delayMs);
  const totalMs = Math.max(1, Math.ceil(t.length / charStep) * delayMs);
  const start = performance.now();

  setShown("");
  while (true) {
    if (typeof document !== "undefined" && document.hidden) {
      setShown(t);
      return;
    }
    const elapsed = performance.now() - start;
    const progress = Math.min(1, elapsed / totalMs);
    const n = Math.min(t.length, Math.max(1, Math.ceil(progress * t.length)));
    setShown(t.slice(0, n));
    if (n >= t.length) break;
    await sleep(Math.min(delayMs, 32));
  }
  setShown(t);
}

export default function Home() {
  const { chats, activeId, loading, error } = useChatStore((s) => s);
  const [search, setSearch]               = useState("");
  const [input, setInput]                 = useState("");
  const [sidebarOpen, setSidebarOpen]     = useState(true);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [openMenuChatId, setOpenMenuChatId] = useState<string | null>(null);
  // Stores the pixel position of the "..." button so we can portal the menu there
  const [menuAnchor, setMenuAnchor] = useState<{ top: number; left: number } | null>(null);
  const [sending, setSending]             = useState(false);
  // Synchronous lock chặn double-submit khi user mash Enter/click trước khi
  // React kịp render disabled button (setSending là async).
  const sendingRef = useRef(false);
  const [renameChatId, setRenameChatId]   = useState<string | null>(null);
  const [renameValue, setRenameValue]     = useState("");
  const [loadingDots, setLoadingDots]     = useState(".");
  const [pendingStatus, setPendingStatus] = useState<string | null>(null);
  const [pendingChatId, setPendingChatId] = useState<string | null>(null);
  const [agentStep, setAgentStep] = useState<{ cur: number; max: number } | null>(null);
  const [streamingChatId, setStreamingChatId] = useState<string | null>(null);
  /** Đang gõ dần câu trả lời đã nhận đủ (Markdown qua BotBubble). */
  const [revealedStreamText, setRevealedStreamText] = useState("");
  /** true trong lúc reveal (kể cả chưa có ký tự nào — hiện bubble chờ). */
  const [postStreamReveal, setPostStreamReveal] = useState(false);
  const [darkMode, toggleDarkMode]       = useThemeMode();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [queryMode, setQueryMode]         = useState<"normal" | "situation">("normal");
  const { user: authUser, getIdToken } = useAuth();
  const [anonId, setAnonId]               = useState<string>("");
  useEffect(() => { setAnonId(getOrCreateUserId()); }, []);
  // user_id ưu tiên Firebase uid khi đã login, fallback demo id ẩn danh
  const userId = authUser?.uid || anonId;
  const [selectedPassages, setSelectedPassages] = useState<Passage[] | null>(null);
  const [selectedMsgIdx, setSelectedMsgIdx]     = useState<number | null>(null);
  const [fullViewPassage, setFullViewPassage]   = useState<Passage | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // ref for the portal menu div — used to detect outside clicks
  const menuRef   = useRef<HTMLDivElement | null>(null);
  // tracks whether the current open-gesture already "consumed" a pointerdown
  const openingRef = useRef(false);

  const activeChat = useMemo(
    () => chats.find((c) => c._id === activeId) ?? null,
    [chats, activeId]
  );

  /** Ẩn bubble assistant vừa lưu trong lúc gõ dần (tránh hiện trùng). */
  const revealHideIdx = useMemo(() => {
    if (!activeChat || !postStreamReveal || streamingChatId !== activeChat._id) return -1;
    const last = activeChat.messages.length - 1;
    if (last < 0 || activeChat.messages[last]?.role !== "assistant") return -1;
    return last;
  }, [activeChat, postStreamReveal, streamingChatId]);

  const { speak, speakingIdx, loadingIdx } = useTTS();
  const {
    startRecording, stopRecording, cancelRecording, clearBlob,
    recording, audioBlob, duration, supported: sttSupported,
  } = useSTT();

  // Nhớ trạng thái thanh bên (mở / thu gọn) giữa các lần vào
  useEffect(() => {
    try { if (localStorage.getItem(SIDEBAR_KEY) === "collapsed") setSidebarOpen(false); } catch {}
  }, []);
  useEffect(() => {
    try { localStorage.setItem(SIDEBAR_KEY, sidebarOpen ? "open" : "collapsed"); } catch {}
  }, [sidebarOpen]);

  // Ô nhập tự giãn: thu về một dòng khi đã gửi xong
  useEffect(() => {
    if (!input && inputRef.current) inputRef.current.style.height = "auto";
  }, [input]);

  useEffect(() => {
    if (!userId) return;
    void chatActions.setUserId(userId);
  }, [userId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeChat?.messages, revealedStreamText]);

  // ── Outside-click logic: close the portal menu on any tap/click outside it ──
  useEffect(() => {
    if (!openMenuChatId) return;

    function handleOutside(e: PointerEvent) {
      // If this is the same gesture that opened the menu, ignore it
      if (openingRef.current) {
        openingRef.current = false;
        return;
      }
      if (menuRef.current && menuRef.current.contains(e.target as Node)) return;
      setOpenMenuChatId(null);
      setMenuAnchor(null);
    }

    document.addEventListener("pointerdown", handleOutside);
    return () => document.removeEventListener("pointerdown", handleOutside);
  }, [openMenuChatId]);

  useEffect(() => {
    if (!sending) { setLoadingDots("."); return; }
    const frames = [".", "..", "..."];
    let i = 0;
    const t = setInterval(() => { i = (i + 1) % frames.length; setLoadingDots(frames[i]); }, 350);
    return () => clearInterval(t);
  }, [sending]);

  useEffect(() => { setMobileSidebarOpen(false); }, [activeId]);

  async function createChat() { await chatActions.createChat(); }
  async function patchChat(id: string, patch: Partial<Chat>) { await chatActions.patchChat(id, patch); }
  async function removeChat(id: string) { await chatActions.removeChat(id); }

  const saveTtsUrl = async (msgIdx: number, url: string) => {
    if (!activeChat) return;
    const updated = activeChat.messages.map((m, i) =>
      i === msgIdx ? { ...m, tts_url: url } : m
    );
    await patchChat(activeChat._id, { messages: updated });
  };

  const handleFeedback = async (msgIdx: number, rating: "up" | "down") => {
    if (!activeChat) return;
    chatActions.updateMessageFeedback(activeChat._id, msgIdx, rating);
    try {
      await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: activeChat._id, msg_idx: msgIdx, rating }),
      });
    } catch (err) {
      console.error("[Feedback]", err);
    }
  };

  const handleRename = async () => {
    if (!renameChatId || !renameValue.trim()) return;
    await patchChat(renameChatId, { title: renameValue.trim() });
    setRenameChatId(null);
    setRenameValue("");
  };

  async function onSend(e?: FormEvent, override?: string) {
    e?.preventDefault();
    // Chặn re-entrant: nếu đang xử lý 1 request trước đó, bỏ qua submit này.
    if (sendingRef.current) return;
    const promptSent = (override ?? input).trim();
    if (!promptSent) return;

    sendingRef.current = true;
    setSending(true);

    // Chưa chọn cuộc trò chuyện nào → tự tạo mới rồi gửi tin vào đó.
    let chat = activeChat;
    if (!chat) {
      try {
        chat = await chatActions.createChat();
      } catch (err) {
        console.error("[onSend] createChat", err);
        chatActions.setError(`Không tạo được cuộc trò chuyện: ${String(err)}`);
        setSending(false);
        sendingRef.current = false;
        return;
      }
    }

    const chatId = chat._id;
    const nextMessages: Message[] = [
      ...chat.messages,
      { role: "user", content: promptSent },
    ];

    try {
      await patchChat(chatId, {
        messages: nextMessages,
        title: autoTitle(nextMessages, chat.title),
      });
    } catch (err) {
      // Lưu thất bại (vd. MongoDB lỗi): nhả khóa để người dùng gửi lại được.
      console.error("[onSend] patchChat", err);
      chatActions.setError(`Không lưu được tin nhắn: ${String(err)}`);
      setSending(false);
      sendingRef.current = false;
      return;
    }

    setInput("");
    setPendingChatId(chatId);
    setPendingStatus(null);

    let fullText = "";
    let metaPassages: Passage[] = [];
    let metaConfidence: number | null = null;
    let metaHallucination = false;
    let metaCacheHit = false;
    let metaMemoriesUsed: MemoryUsed[] = [];

    const persistAssistant = async (content: string) => {
      await patchChat(chatId, {
        messages: [...nextMessages, {
          role: "assistant",
          content,
          passages: metaPassages,
          tts_url: null,
          confidence_score: metaConfidence,
          hallucination_warning: metaHallucination,
          cache_hit: metaCacheHit,
          memories_used: metaMemoriesUsed,
        }],
      });
    };

    try {
      const token = await getIdToken();
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const res = await fetch("/api/stream", {
        method: "POST",
        headers,
        body: JSON.stringify({
          prompt: promptSent,
          history: nextMessages,
          query_mode: queryMode,
          user_id: userId,
          chat_id: chatId,
        }),
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let   buffer  = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;
          try {
            const evt = JSON.parse(raw) as {
              type: "meta" | "token" | "done" | "error" | "status";
              text?: string; passages?: Passage[]; message?: string;
              iteration?: number; max?: number;
              confidence_score?: number; hallucination_warning?: boolean; cache_hit?: boolean;
              memories_used?: MemoryUsed[];
            };
            if (evt.type === "status") {
              setPendingStatus(evt.text ?? "");
              if (evt.iteration != null && evt.max != null)
                setAgentStep({ cur: evt.iteration, max: evt.max });
            } else if (evt.type === "meta") {
              setPendingStatus(null); setPendingChatId(null); setAgentStep(null);
              setStreamingChatId(chatId);
              metaPassages    = evt.passages ?? [];
              metaConfidence  = evt.confidence_score ?? null;
              metaHallucination = evt.hallucination_warning ?? false;
              metaCacheHit    = evt.cache_hit ?? false;
              metaMemoriesUsed = evt.memories_used ?? [];
            } else if (evt.type === "token") {
              fullText += evt.text ?? "";
            } else if (evt.type === "error") {
              setPendingStatus(null); setPendingChatId(null); setAgentStep(null);
              setStreamingChatId(chatId);
              fullText += `\n\n⚠️ ${evt.message}`;
              break;
            } else if (evt.type === "done") {
              break;
            }
          } catch { /* ignore */ }
        }
      }

      const answer =
        fullText.trim() ||
        "Mình chưa thể tạo câu trả lời. Vui lòng thử lại.";

      // Lưu ngay — không phụ thuộc animation (chuyển tab / chuyển hội thoại vẫn có câu trả lời)
      await persistAssistant(answer);

      setStreamingChatId(chatId);
      setRevealedStreamText("");
      setPostStreamReveal(true);
      await revealAnswerProgressively(answer, setRevealedStreamText);
      setPostStreamReveal(false);
      setRevealedStreamText("");
      setStreamingChatId(null);
    } catch (err) {
      console.error("[onSend]", err);
      const errMsg = "Mình gặp lỗi kết nối. Vui lòng thử lại sau.";
      metaPassages = [];
      await persistAssistant(errMsg);
      setStreamingChatId(chatId);
      setRevealedStreamText("");
      setPostStreamReveal(true);
      await revealAnswerProgressively(errMsg, setRevealedStreamText);
      setPostStreamReveal(false);
      setRevealedStreamText("");
      setStreamingChatId(null);
    } finally {
      setSending(false); setPendingStatus(null); setPendingChatId(null);
      setAgentStep(null); setStreamingChatId(null);
      setRevealedStreamText("");
      setPostStreamReveal(false);
      sendingRef.current = false;
    }
  }

  const sendVoice = async () => {
    if (!audioBlob) return;
    setSending(true);
    const formData = new FormData();
    formData.append("file", audioBlob, "audio.webm");
    try {
      const res  = await fetch("/api/stt", { method: "POST", body: formData });
      const data = await res.json() as { text: string };
      if (data.text) { setInput(data.text); clearBlob(); }
    } catch (err) {
      console.error("[STT]", err);
    } finally {
      setSending(false); clearBlob();
    }
  };

  const filtered      = chats.filter((c) => (c.title ?? "").toLowerCase().includes(search.toLowerCase()));
  const pinnedChats   = filtered.filter((c) => c.pinned);
  const unpinnedChats = filtered.filter((c) => !c.pinned);

  // ── Portal-based ChatMenu ────────────────────────────────────────────────────
  // Renders into document.body so it is never clipped by overflow:hidden parents.
  function ChatMenuPortal({ chat }: { chat: Chat }) {
    if (openMenuChatId !== chat._id || !menuAnchor) return null;

    const menuContent = (
      <div
        ref={menuRef}
        style={{
          position: "fixed",
          top: menuAnchor.top,
          left: menuAnchor.left,
          width: 200,
          borderRadius: 10,
          padding: 4,
          background: "var(--bg-surface)",
          border: "1px solid var(--border)",
          boxShadow: "0 8px 24px rgba(14, 19, 27, 0.16)",
          zIndex: 9000,
        }}
      >
        {/* Đổi tên */}
        <button
          className="w-full rounded-lg px-3 py-2 text-left text-sm"
          style={{ color: "var(--text-primary)" }}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            setRenameChatId(chat._id);
            setRenameValue(chat.title ?? "");
            setOpenMenuChatId(null);
            setMenuAnchor(null);
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
        >
          <div className="flex items-center gap-2"><Pencil size={16} />Đổi tên</div>
        </button>

        {/* Ghim / Bỏ ghim */}
        <button
          className="w-full rounded-lg px-3 py-2 text-left text-sm"
          style={{ color: "var(--text-primary)" }}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={async (e) => {
            e.stopPropagation();
            await patchChat(chat._id, { pinned: !chat.pinned });
            setOpenMenuChatId(null);
            setMenuAnchor(null);
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
        >
          <div className="flex items-center gap-2"><Pin size={16} />Ghim / Bỏ ghim</div>
        </button>

        {/* Xóa */}
        <button
          className="w-full rounded-lg px-3 py-2 text-left text-sm"
          style={{ color: "var(--danger-text)", background: "transparent" }}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={async (e) => {
            e.stopPropagation();
            await removeChat(chat._id);
            setOpenMenuChatId(null);
            setMenuAnchor(null);
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--danger-bg)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
        >
          <div className="flex items-center gap-2"><Trash2 size={16} />Xóa</div>
        </button>
      </div>
    );

    if (typeof document === "undefined") return null;
    return createPortal(menuContent, document.body);
  }

  function ChatItem({ chat }: { chat: Chat }) {
    const active = activeId === chat._id;
    const menuOpen = openMenuChatId === chat._id;
    return (
      <div
        className={`group relative flex h-9 items-center rounded-md transition-colors ${active ? "" : "hover:bg-[var(--bg-hover)]"}`}
        style={active ? { background: "var(--bg-surface)", boxShadow: "var(--shadow-sm)" } : undefined}
      >
        <button
          onClick={() => { chatActions.setActiveChat(chat._id); setMobileSidebarOpen(false); }}
          aria-current={active ? "page" : undefined}
          className="h-full min-w-0 flex-1 truncate rounded-md pl-2.5 pr-1 text-left text-sm"
          style={{ color: active ? "var(--text-primary)" : "var(--text-secondary)", fontWeight: active ? 500 : 400 }}
        >
          {chat.title ?? "Cuộc trò chuyện mới"}
        </button>

        {/* Nút "…" — tính vị trí để menu (portal) không bị cắt bởi overflow */}
        <button
          aria-label={`Tuỳ chọn cho “${chat.title ?? "đoạn chat"}”`}
          aria-expanded={menuOpen}
          onPointerDown={(e) => {
            e.stopPropagation();
            // Đánh dấu cử chỉ mở menu để handler click-ra-ngoài bỏ qua
            openingRef.current = true;
          }}
          onClick={(e) => {
            e.stopPropagation();
            if (menuOpen) {
              setOpenMenuChatId(null);
              setMenuAnchor(null);
              openingRef.current = false;
              return;
            }
            const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
            const MENU_WIDTH = 200;
            let left = rect.right - MENU_WIDTH;
            if (left < 8) left = 8;
            if (left + MENU_WIDTH > window.innerWidth - 8) left = window.innerWidth - MENU_WIDTH - 8;
            setMenuAnchor({ top: rect.bottom + 4, left });
            setOpenMenuChatId(chat._id);
          }}
          className={`mr-1 flex h-7 w-7 shrink-0 items-center justify-center rounded transition-opacity hover:bg-[var(--bg-hover)] focus-visible:opacity-100 ${
            menuOpen || active ? "opacity-100" : "opacity-100 md:opacity-0 md:group-hover:opacity-100"}`}
          style={{ color: "var(--text-muted)" }}
        >
          <MoreHorizontal size={16} />
        </button>

        <ChatMenuPortal chat={chat} />
      </div>
    );
  }

  const BrandMark = ({ size = 32 }: { size?: number }) => (
    <span
      className="flex shrink-0 items-center justify-center overflow-hidden rounded-lg"
      style={{ width: size, height: size, background: "#FFFFFF", boxShadow: "0 0 0 1px var(--border)" }}
    >
      <Image src="/logo-mark.png" alt="" width={size - 4} height={size - 4} />
    </span>
  );

  const sectionLabel = (text: string) => (
    <div className="px-2.5 pb-1 pt-4 text-xs font-medium first:pt-1" style={{ color: "var(--text-muted)" }}>{text}</div>
  );

  const renderSidebarPanel = (mobile: boolean) => (
    <div className="flex h-full w-full min-w-0 flex-col">
      {/* Thương hiệu + đóng/thu gọn — cùng một hàng với header bên phải (h-14) */}
      <div className="flex h-14 shrink-0 items-center justify-between gap-2 pl-3 pr-2">
        <div className="flex min-w-0 items-center gap-2.5">
          <BrandMark />
          <span className="truncate text-[15px] font-semibold tracking-tight" style={{ color: "var(--text-primary)" }}>
            Chatbot VNLaw
          </span>
        </div>
        <SidebarIconButton
          label={mobile ? "Đóng danh sách đoạn chat" : "Thu gọn thanh bên"}
          onClick={() => (mobile ? setMobileSidebarOpen(false) : setSidebarOpen(false))}
        >
          {mobile ? <X size={18} /> : <PanelLeftClose size={18} />}
        </SidebarIconButton>
      </div>

      <div className="shrink-0 space-y-1 px-2 pb-2">
        <button
          onClick={() => { createChat(); setMobileSidebarOpen(false); }}
          className="flex h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-sm font-medium transition-colors hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-primary)" }}
        >
          <SquarePen size={17} aria-hidden />Đoạn chat mới
        </button>
        <label
          className="flex h-9 items-center gap-2.5 rounded-md px-2.5 transition-shadow focus-within:shadow-[var(--focus-ring)]"
          style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}
        >
          <Search size={16} aria-hidden style={{ color: "var(--text-muted)" }} />
          <input
            ref={searchRef}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Tìm đoạn chat"
            aria-label="Tìm đoạn chat"
            className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--text-muted)]"
            style={{ color: "var(--text-primary)", boxShadow: "none" }}
          />
          {search && (
            <button type="button" onClick={() => setSearch("")} aria-label="Xoá từ khoá tìm kiếm"
              className="rounded p-0.5 hover:bg-[var(--bg-hover)]" style={{ color: "var(--text-muted)" }}>
              <X size={14} />
            </button>
          )}
        </label>
      </div>

      {error && (
        <div className="mx-2 mb-2 rounded-md px-3 py-2 text-xs"
          style={{ background: "var(--danger-bg)", border: "1px solid var(--danger-border)", color: "var(--danger-text)" }}>
          {error}
        </div>
      )}

      <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-3" aria-label="Các đoạn chat">
        {pinnedChats.length > 0 && (
          <>
            {sectionLabel("Đã ghim")}
            <div className="space-y-0.5">{pinnedChats.map((chat) => <ChatItem key={chat._id} chat={chat} />)}</div>
          </>
        )}
        {unpinnedChats.length > 0 && (
          <>
            {sectionLabel("Gần đây")}
            <div className="space-y-0.5">{unpinnedChats.map((chat) => <ChatItem key={chat._id} chat={chat} />)}</div>
          </>
        )}
        {filtered.length === 0 && (
          <p className="px-2.5 pt-3 text-[13px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
            {search
              ? `Không có đoạn chat nào khớp “${search}”.`
              : "Chưa có đoạn chat nào. Câu hỏi đầu tiên của bạn sẽ được lưu ở đây."}
          </p>
        )}
      </nav>
    </div>
  );

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)", color: "var(--text-primary)" }}>
      <div className="mx-auto flex h-screen">

        {/* MOBILE SIDEBAR (ngăn kéo) */}
        {mobileSidebarOpen && (
          <>
            <div
              className="fixed inset-0 z-40 md:hidden"
              style={{ background: "rgba(14, 19, 27, 0.45)" }}
              onClick={() => setMobileSidebarOpen(false)}
              aria-hidden
            />
            <aside
              className="fixed left-0 top-0 z-50 flex h-full md:hidden"
              role="dialog" aria-modal="true" aria-label="Danh sách đoạn chat"
              style={{
                width: "min(85vw, 300px)",
                background: "var(--bg-sidebar)",
                borderRight: "1px solid var(--border)",
                boxShadow: "8px 0 24px rgba(14, 19, 27, 0.18)",
                animation: "slideInLeft 0.2s ease-out",
              }}
            >
              {renderSidebarPanel(true)}
            </aside>
          </>
        )}

        {/* DESKTOP SIDEBAR — mở: 272px · thu gọn: thanh icon 60px */}
        <aside
          className="relative hidden shrink-0 overflow-hidden transition-[width] duration-200 ease-out md:flex"
          style={{
            width: sidebarOpen ? SIDEBAR_W : RAIL_W,
            height: "100vh",
            background: "var(--bg-sidebar)",
            borderRight: "1px solid var(--border)",
          }}
          aria-label="Thanh bên"
        >
          {sidebarOpen ? (
            // Bề rộng cố định: khi mở, nội dung được "lộ ra" thay vì bị bóp méo trong lúc chuyển động
            <div className="flex h-full shrink-0" style={{ width: SIDEBAR_W }}>{renderSidebarPanel(false)}</div>
          ) : (
            <div className="flex h-full w-full flex-col items-center gap-1 pt-3">
              <button
                onClick={() => setSidebarOpen(true)}
                title="Mở thanh bên" aria-label="Mở thanh bên"
                className="group relative mb-2 flex h-10 w-10 items-center justify-center rounded-lg transition-colors hover:bg-[var(--bg-hover)]"
              >
                <span className="transition-opacity group-hover:opacity-0 group-focus-visible:opacity-0"><BrandMark /></span>
                <PanelLeftOpen size={18} aria-hidden
                  className="absolute opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
                  style={{ color: "var(--text-secondary)" }} />
              </button>
              <SidebarIconButton label="Đoạn chat mới" onClick={createChat}><SquarePen size={18} /></SidebarIconButton>
              <SidebarIconButton
                label="Tìm đoạn chat"
                onClick={() => { setSidebarOpen(true); setTimeout(() => searchRef.current?.focus(), 220); }}
              >
                <Search size={18} />
              </SidebarIconButton>
            </div>
          )}
        </aside>

        {/* MAIN CONTENT */}
        <main className="flex flex-1 flex-col overflow-hidden" style={{ background: "var(--bg-main)", minWidth: 0 }}>
          <div className="flex flex-1 overflow-hidden">
            <div className="flex flex-1 flex-col overflow-hidden min-w-0">

              {/* Topbar */}
              <header className="flex h-14 shrink-0 items-center justify-between gap-2 px-3 md:px-5"
                style={{ borderBottom: "1px solid var(--border)" }}>
                <div className="flex min-w-0 items-center gap-2">
                  <button
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md transition-colors hover:bg-[var(--bg-hover)] md:hidden"
                    style={{ color: "var(--text-secondary)" }}
                    onClick={() => setMobileSidebarOpen(true)}
                    aria-label="Mở danh sách đoạn chat"
                  >
                    <Menu size={18} />
                  </button>
                  <h1 className="truncate text-[15px] font-medium" style={{ color: "var(--text-primary)" }}>
                    {activeChat?.title ?? "Hỏi đáp pháp luật"}
                  </h1>
                </div>
                <div className="flex shrink-0 items-center gap-0.5 md:gap-1">
                  <a
                    href={activeChat ? `/office?chat=${encodeURIComponent(activeChat._id)}` : "/office?live=1"}
                    title="Xem văn phòng luật 3D (tiếp tục hội thoại đang mở)"
                    className="inline-flex h-9 items-center gap-1.5 rounded-md px-2.5 text-sm transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
                    style={{ color: "var(--text-secondary)" }}>
                    <Building2 size={16} aria-hidden /><span className="hidden lg:inline">Văn phòng 3D</span>
                  </a>
                  {userId && <MemoryPanelButton userId={userId} />}
                  <button
                    onClick={toggleDarkMode}
                    className="flex h-9 w-9 items-center justify-center rounded-md transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
                    style={{ color: "var(--text-secondary)" }}
                    title={darkMode ? "Chuyển sang nền sáng" : "Chuyển sang nền tối"}
                    aria-label={darkMode ? "Chuyển sang nền sáng" : "Chuyển sang nền tối"}
                  >
                    {darkMode ? <Sun size={17} /> : <Moon size={17} />}
                  </button>
                  <span className="mx-1 hidden h-5 w-px md:block" style={{ background: "var(--border)" }} aria-hidden />
                  <AuthButton />
                </div>
              </header>

              {/* Messages */}
              <div className="flex-1 overflow-y-auto">
                <div className="mx-auto w-full px-4 py-6 md:py-10" style={{ maxWidth: "var(--reading-width)" }}>
                {activeChat?.messages?.length ? (
                  <div className="space-y-8">
                    {activeChat.messages.map((m, idx) => {
                      if (idx === revealHideIdx) return null;
                      return (
                      <MessageRow
                        key={idx} m={m} idx={idx} chatId={activeChat._id}
                        isSelected={selectedMsgIdx === idx}
                        onSelect={(passages) => {
                          if (selectedMsgIdx === idx) { setSelectedPassages(null); setSelectedMsgIdx(null); }
                          else { setSelectedPassages(passages); setSelectedMsgIdx(idx); }
                        }}
                        onSpeak={speak}
                        isSpeaking={speakingIdx === idx}
                        isLoadingTTS={loadingIdx === idx}
                        onSaveTtsUrl={saveTtsUrl}
                        isAnyTTSActive={speakingIdx !== null || loadingIdx !== null}
                        onSaveFeedback={m.role === "assistant" ? handleFeedback : undefined}
                      />
                      );
                    })}
                    {streamingChatId === activeChat._id && (postStreamReveal || revealedStreamText.length > 0) && (
                      <BotBubble content={revealedStreamText || " "} />
                    )}
                    {pendingChatId === activeChat._id && pendingStatus && (
                      <div className="flex items-center gap-2.5 text-sm" role="status" aria-live="polite"
                        style={{ color: "var(--text-secondary)" }}>
                        <span className="relative flex h-2 w-2 shrink-0" aria-hidden>
                          <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60"
                            style={{ background: "var(--accent)" }} />
                          <span className="relative inline-flex h-2 w-2 rounded-full" style={{ background: "var(--accent)" }} />
                        </span>
                        <span>{pendingStatus}</span>
                        {agentStep != null && (
                          <span style={{ color: "var(--text-muted)" }}>bước {agentStep.cur}/{agentStep.max}</span>
                        )}
                      </div>
                    )}
                    <div ref={bottomRef} />
                  </div>
                ) : (
                  <section className="pt-6 md:pt-16" aria-labelledby="empty-title">
                    <h2 id="empty-title" className="text-[26px] font-semibold leading-tight tracking-tight md:text-[32px]"
                      style={{ color: "var(--text-primary)" }}>
                      Bạn đang gặp vấn đề pháp lý gì?
                    </h2>
                    <p className="mt-3 max-w-[34rem] text-[15px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                      Hỏi bằng lời của bạn. Mỗi câu trả lời dẫn điều luật cụ thể để bạn tự kiểm tra lại.
                    </p>
                    <ul className="mt-8 overflow-hidden rounded-xl" style={{ border: "1px solid var(--border)", background: "var(--bg-surface)" }}>
                      {SUGGESTIONS.map((s, i) => (
                        <li key={s.q} style={i ? { borderTop: "1px solid var(--border)" } : undefined}>
                          <button
                            type="button" disabled={sending}
                            onClick={() => onSend(undefined, s.q)}
                            className="flex w-full flex-col gap-0.5 px-4 py-3.5 text-left transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-60 sm:flex-row sm:items-baseline sm:gap-4"
                          >
                            <span className="w-40 shrink-0 text-[13px]" style={{ color: "var(--text-muted)" }}>{s.topic}</span>
                            <span className="text-[15px]" style={{ color: "var(--text-primary)" }}>{s.q}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
                </div>
              </div>

              {/* Input form */}
              <form onSubmit={onSend} className="shrink-0 px-3 pb-3 pt-2 md:px-5 md:pb-4">
                <div className="mx-auto w-full" style={{ maxWidth: "var(--reading-width)" }}>
                <div className="mb-2 flex flex-wrap items-center gap-3">
                  <div role="radiogroup" aria-label="Kiểu câu hỏi" className="inline-flex rounded-lg p-0.5"
                    style={{ background: "var(--bg-hover)" }}>
                    {([["normal", "Hỏi đáp"], ["situation", "Phân tích tình huống"]] as const).map(([mode, label]) => (
                      <button key={mode} type="button" role="radio" aria-checked={queryMode === mode}
                        onClick={() => setQueryMode(mode)}
                        className="rounded-md px-3 py-1 text-[13px] transition-colors"
                        style={queryMode === mode
                          ? { background: "var(--bg-surface)", color: "var(--text-primary)", boxShadow: "var(--shadow-sm)", fontWeight: 500 }
                          : { color: "var(--text-secondary)" }}>
                        {label}
                      </button>
                    ))}
                  </div>
                  {queryMode === "situation" && (
                    <span className="hidden text-[13px] sm:inline" style={{ color: "var(--text-muted)" }}>
                      Kể rõ ai, đã làm gì, trong hoàn cảnh nào và hậu quả ra sao.
                    </span>
                  )}
                </div>

                <div className="flex items-end gap-2 rounded-2xl p-2 transition-shadow focus-within:shadow-[var(--focus-ring)]"
                  style={{ background: "var(--bg-input)", border: "1px solid var(--border-strong)", boxShadow: "var(--shadow-input)" }}>
                  {!recording && !audioBlob && (
                    <textarea
                      ref={inputRef}
                      rows={1}
                      value={input}
                      onChange={(e) => {
                        setInput(e.target.value);
                        e.target.style.height = "auto";
                        e.target.style.height = `${Math.min(e.target.scrollHeight, 200)}px`;
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                          e.preventDefault();
                          // Chặn double-fire khi đang xử lý request trước
                          if (sending || sendingRef.current) return;
                          onSend();
                        }
                      }}
                      disabled={sending}
                      aria-label="Câu hỏi của bạn"
                      placeholder={queryMode === "situation" ? "Mô tả tình huống của bạn…" : "Nhập câu hỏi pháp luật…"}
                      className="max-h-[200px] min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-[15px] leading-relaxed outline-none placeholder:text-[var(--text-muted)] disabled:opacity-70"
                      style={{ color: "var(--text-primary)", minWidth: 0, boxShadow: "none" }}
                    />
                  )}
                  {recording && (
                    <>
                      <div className="flex min-h-[40px] flex-1 items-center gap-2 px-2 md:gap-3">
                        <span className="relative flex h-3 w-3 shrink-0" aria-hidden>
                          <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-75" style={{ background: "var(--accent)" }} />
                          <span className="relative inline-flex h-3 w-3 rounded-full" style={{ background: "var(--accent)" }} />
                        </span>
                        <LiveWaveform recording={recording} />
                        <span className="shrink-0 text-xs tabular-nums" style={{ color: "var(--accent-text)" }}>
                          {String(Math.floor(duration / 60)).padStart(2, "0")}:{String(duration % 60).padStart(2, "0")}
                        </span>
                      </div>
                      <button type="button" onClick={stopRecording} title="Dừng ghi âm" aria-label="Dừng ghi âm"
                        className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full"
                        style={{ background: "var(--accent)", color: "var(--text-on-accent)" }}>
                        <Square size={14} fill="currentColor" />
                      </button>
                    </>
                  )}
                  {!recording && audioBlob && (
                    <div className="flex min-h-[40px] flex-1 items-center gap-2 px-2">
                      <VoiceMessage audioBlob={audioBlob} onCancel={cancelRecording} />
                    </div>
                  )}
                  {sttSupported && !recording && !audioBlob && (
                    <button type="button" onClick={startRecording} title="Hỏi bằng giọng nói" aria-label="Hỏi bằng giọng nói"
                      className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-[var(--bg-hover)]"
                      style={{ color: "var(--text-secondary)" }}>
                      <Mic size={18} />
                    </button>
                  )}
                  <button
                    type={audioBlob ? "button" : "submit"}
                    onClick={audioBlob ? sendVoice : undefined}
                    disabled={loading || sending || (!audioBlob && !input.trim())}
                    title="Gửi câu hỏi" aria-label="Gửi câu hỏi"
                    className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition-colors enabled:hover:bg-[var(--accent-hover)] disabled:cursor-not-allowed"
                    style={(loading || sending || (!audioBlob && !input.trim()))
                      ? { background: "var(--bg-hover)", color: "var(--text-muted)" }
                      : { background: "var(--accent)", color: "var(--text-on-accent)" }}
                  >
                    {sending ? <span className="text-xs" aria-hidden>{loadingDots}</span> : <ArrowUp size={18} strokeWidth={2.25} />}
                  </button>
                </div>
                <p className="mt-2 text-center text-xs" style={{ color: "var(--text-muted)" }}>
                  Thông tin chỉ để tham khảo, không thay thế tư vấn của luật sư.
                </p>
                </div>
              </form>
            </div>

            {/* Passage panel */}
            {selectedPassages && (
              <>
                <div className="hidden md:flex">
                  <PassagePanel passages={selectedPassages} onClose={() => { setSelectedPassages(null); setSelectedMsgIdx(null); }} />
                </div>
                <div className="fixed inset-0 z-50 flex flex-col md:hidden" style={{ background: "var(--bg-sidebar)" }}>
                  <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "0.5px solid var(--border)" }}>
                    <div className="flex items-center gap-2 text-base font-bold" style={{ color: "var(--text-primary)" }}>
                      <BookOpen className="h-5 w-5" style={{ color: "var(--accent-text)" }} aria-hidden />Căn cứ pháp lý
                    </div>
                    <button onClick={() => { setSelectedPassages(null); setSelectedMsgIdx(null); }}
                      className="rounded-lg px-2 py-1 text-sm"
                      style={{ background: "var(--bg-hover)", color: "var(--text-muted)" }}>
                      <X size={18} />
                    </button>
                  </div>
                  <div className="flex-1 overflow-y-auto">
                    <PassagePanel passages={selectedPassages} onClose={() => { setSelectedPassages(null); setSelectedMsgIdx(null); }} mobileEmbedded />
                  </div>
                </div>
              </>
            )}
          </div>
        </main>

        {fullViewPassage && <PassageFullModal passage={fullViewPassage} onClose={() => setFullViewPassage(null)} />}
      </div>

      {/* Rename modal */}
      {renameChatId && (
        <div
          className="fixed inset-0 flex items-center justify-center p-4"
          style={{ background: "rgba(0,0,0,0.5)", zIndex: 300 }}
          onClick={(e) => { if (e.target === e.currentTarget) { setRenameChatId(null); } }}
        >
          <div className="w-full max-w-sm rounded-xl p-4 shadow-xl md:max-w-md"
            style={{ background: "var(--bg-surface)", border: "0.5px solid var(--border-strong)" }}>
            <div className="mb-3 text-base font-semibold" style={{ color: "var(--text-primary)" }}>
              Đổi tên cuộc trò chuyện
            </div>
            <input
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleRename(); } }}
              className="w-full rounded-lg px-3 py-2 text-sm outline-none"
              style={{ background: "var(--bg-input)", border: "0.5px solid var(--border)", color: "var(--text-primary)" }}
              placeholder="Nhập tên mới..."
              autoFocus
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => { setRenameChatId(null); setRenameValue(""); }}
                className="rounded-lg px-3 py-2 text-sm transition"
                style={{ background: "var(--bg-hover)", color: "var(--text-secondary)" }}
              >Hủy</button>
              <button
                onClick={handleRename}
                className="rounded-lg px-3 py-2 text-sm font-medium transition"
                style={{ background: "var(--accent)", color: "var(--text-on-accent)" }}
              >Lưu</button>
            </div>
          </div>
        </div>
      )}

      <style>{`
        @keyframes slideInLeft {
          from { transform: translateX(-100%); opacity: 0; }
          to   { transform: translateX(0);    opacity: 1; }
        }
      `}</style>
    </div>
  );
}