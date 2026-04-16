"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { chatActions, Chat, Message, useChatStore } from "@/store/chat-store";
import {
  Plus,
  Search,
  MoreHorizontal,
  Pin,
  Trash2,
  Pencil,
  MessageCircle,
  ChevronLeft,
  ChevronRight,
  Clock,
} from "lucide-react";


/* ─────────────────────────────────────────────
   Inline SVG avatars (unchanged)
───────────────────────────────────────────── */
const USER_AVATAR_SRC = toSvgDataUri(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<defs>
  <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#534AB7"/>
    <stop offset="1" stop-color="#1d4ed8"/>
  </linearGradient>
</defs>
<rect width="64" height="64" rx="32" fill="url(#g)"/>
<circle cx="32" cy="26" r="12" fill="#fff" opacity="0.95"/>
<path d="M14 54c3.5-11 13.2-16 18-16s14.5 5 18 16" fill="#fff" opacity="0.95"/>
</svg>`
);

const BOT_AVATAR_SRC = toSvgDataUri(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="32" fill="#0f172a"/>
<path d="M22 18h20a10 10 0 0 1 10 10v10a12 12 0 0 1-12 12H24A12 12 0 0 1 12 38V28a10 10 0 0 1 10-10z" fill="#e2e8f0"/>
<circle cx="26" cy="34" r="4" fill="#0f172a"/>
<circle cx="38" cy="34" r="4" fill="#0f172a"/>
<path d="M28 46c2.5 2 5.5 2 8 0" stroke="#0f172a" stroke-width="3" fill="none" stroke-linecap="round"/>
<path d="M32 12v8" stroke="#e2e8f0" stroke-width="4" stroke-linecap="round"/>
</svg>`
);

/* ─────────────────────────────────────────────
   Component
───────────────────────────────────────────── */
export default function Home() {
  const { chats, activeId, loading, error } = useChatStore((s) => s);
  const [search, setSearch] = useState("");
  const [input, setInput] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [openMenuChatId, setOpenMenuChatId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [renameChatId, setRenameChatId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [loadingDots, setLoadingDots] = useState(".");
  const [pendingStatus, setPendingStatus] = useState<string | null>(null);
  const [pendingChatId, setPendingChatId] = useState<string | null>(null);
  const [streamingText, setStreamingText] = useState("");
  const [streamingChatId, setStreamingChatId] = useState<string | null>(null);
  const [darkMode, setDarkMode] = useState(true);
  const [queryMode, setQueryMode] = useState<"normal" | "situation">("normal");
  const menuRef = useRef<HTMLDivElement | null>(null);

  const activeChat = useMemo(
    () => chats.find((c) => c._id === activeId) ?? null,
    [chats, activeId]
  );

  /* Toggle dark/light on <html> */
  useEffect(() => {
    const root = document.documentElement;
    if (darkMode) {
      root.classList.add("dark");
    } else {
      root.classList.remove("dark");
    }
  }, [darkMode]);

  const handleRename = async () => {
    if (!renameChatId) return;
    const value = renameValue.trim();
    if (!value) return;
    await patchChat(renameChatId, { title: value });
    setRenameChatId(null);
    setRenameValue("");
  };

  useEffect(() => { void chatActions.ensureChatsLoaded(); }, []);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (!menuRef.current) return;
      if (!menuRef.current.contains(event.target as Node)) setOpenMenuChatId(null);
    }
    if (openMenuChatId) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [openMenuChatId]);

  useEffect(() => {
    if (!sending) { setLoadingDots("."); return; }
    const frames = [".", "..", "..."];
    let idx = 0;
    const timer = setInterval(() => { idx = (idx + 1) % frames.length; setLoadingDots(frames[idx]); }, 350);
    return () => clearInterval(timer);
  }, [sending]);

  async function createChat() { await chatActions.createChat(); }
  async function patchChat(id: string, patch: Partial<Chat>) { await chatActions.patchChat(id, patch); }
  async function removeChat(id: string) { await chatActions.removeChat(id); }

  async function onSend(e: FormEvent) {
    e.preventDefault();
    if (!input.trim()) return;
    if (!activeChat) { await createChat(); return; }
    const nextMessages = [...activeChat.messages, { role: "user" as const, content: input.trim() }];
    setInput("");
    setSending(true);
    setPendingChatId(activeChat._id);
    setPendingStatus("⏳ Đang phân tích yêu cầu...");
    let statusIdx = 0;
    const statusSteps = [
      "⏳ Đang phân tích yêu cầu...",
      "🔍 Đang tìm kiếm thông tin pháp lý...",
      "🧠 Đang tổng hợp và lập luận...",
      "✍️ Đang soạn câu trả lời...",
    ];
    const statusTimer = setInterval(() => {
      statusIdx = Math.min(statusIdx + 1, statusSteps.length - 1);
      setPendingStatus(statusSteps[statusIdx]);
    }, 1200);
    try {
      await patchChat(activeChat._id, { messages: nextMessages, title: autoTitle(nextMessages, activeChat.title) });
      const ragRes = await fetch("/api/rag", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: input.trim(), history: nextMessages, query_mode: queryMode }),
      });
      const ragData = (await ragRes.json().catch(() => ({}))) as { answer?: string; error?: string; detail?: string };
      const assistant = {
        role: "assistant" as const,
        content: ragData.answer?.trim() ||
          `Mình chưa thể lấy phản hồi từ RAG backend.\n\n${ragData.error ?? ""}\n${ragData.detail ?? ""}`.trim(),
      };
      clearInterval(statusTimer);
      setPendingStatus(null);
      setPendingChatId(null);
      setStreamingChatId(activeChat._id);
      setStreamingText("");
      const full = assistant.content;
      const chunkSize = 3;
      for (let i = 0; i < full.length; i += chunkSize) {
        setStreamingText(full.slice(0, i + chunkSize));
        await new Promise((resolve) => setTimeout(resolve, 15));
      }
      await patchChat(activeChat._id, { messages: [...nextMessages, assistant] });
      setStreamingText("");
      setStreamingChatId(null);
    } finally {
      clearInterval(statusTimer);
      setSending(false);
      setPendingStatus(null);
      setPendingChatId(null);
      setStreamingText("");
      setStreamingChatId(null);
    }
  }

  const filtered = chats.filter((c) => (c.title ?? "").toLowerCase().includes(search.toLowerCase()));
  const pinnedChats = filtered.filter((c) => c.pinned);
  const unpinnedChats = filtered.filter((c) => !c.pinned);

  /* ─── Shared menu dropdown ─── */
  const ChatMenu = ({ chat }: { chat: Chat }) =>
    openMenuChatId === chat._id ? (
      <div
        ref={menuRef}
        style={{ 
          position: "absolute", 
          right: "-200px", 
          top: "2.5rem",
          background: "var(--bg-surface)",
          border: "0.5px solid var(--border-strong)", 
        }}
        className="z-30 w-52 rounded-xl p-1 shadow-2xl"
      >
        <button
          onClick={() => { setRenameChatId(chat._id); setRenameValue(chat.title ?? ""); setOpenMenuChatId(null); }}
          className="w-full rounded-lg px-3 py-2 text-left text-sm"
          style={{ color: "var(--text-primary)" }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
        >
          <div className="flex items-center gap-2">
            <Pencil size={16} />
            Đổi tên
          </div>
        </button>
        <button
          onClick={async () => { await patchChat(chat._id, { pinned: !chat.pinned }); setOpenMenuChatId(null); }}
          className="w-full rounded-lg px-3 py-2 text-left text-sm"
          style={{ color: "var(--text-primary)" }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
        >
          <div className="flex items-center gap-2">
            <Pin size={16} />
            Ghim / Bỏ ghim
          </div>
        </button>
        <button
          onClick={async () => { await removeChat(chat._id); setOpenMenuChatId(null); }}
          className="w-full rounded-lg px-3 py-2 text-left text-sm"
          style={{ color: "var(--danger-text)", background: "transparent" }}
          onMouseEnter={(e) => { e.currentTarget.style.background = "var(--danger-bg)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
        >
          <div className="flex items-center gap-2">
            <Trash2 size={16} />
            Xóa
          </div>
        </button>
      </div>
    ) : null;

  /* ─── Chat list item ─── */
  const ChatItem = ({ chat }: { chat: Chat }) => (
    <div
      className="group relative flex items-center gap-1 rounded-lg"
      style={{
        background: activeId === chat._id ? "var(--bg-surface)" : "transparent",
        overflow: "visible",
      }}
      onMouseEnter={(e) => { if (activeId !== chat._id) (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)"; }}
      onMouseLeave={(e) => { if (activeId !== chat._id) (e.currentTarget as HTMLElement).style.background = "transparent"; }}
    >
      <button
        onClick={() => chatActions.setActiveChat(chat._id)}
        className="flex-1 truncate rounded-lg px-3 py-2 text-left text-sm"
        style={{ color: activeId === chat._id ? "var(--text-primary)" : "var(--text-secondary)" }}
      >
        {chat.title ?? "Cuộc trò chuyện mới"}
      </button>
      <button
        onClick={() => setOpenMenuChatId((prev) => (prev === chat._id ? null : chat._id))}
        className="mr-1 w-8 rounded-md py-1 text-center opacity-0 transition group-hover:opacity-100"
        style={{ color: "var(--text-muted)" }}
      >
        <MoreHorizontal size={18} />
      </button>
      <ChatMenu chat={chat} />
    </div>
  );

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)", color: "var(--text-primary)" }}>
      <div className="mx-auto flex h-screen">

        {/* ── Sidebar ── */}
        <aside
          className="relative transition-all duration-200"
          style={{
            width: sidebarOpen ? 340 : 56,
            padding: sidebarOpen ? 12 : 8,
            display: "flex",
            flexDirection: "column",
            gap: 10,
            height: "100vh",
            overflow: "visible",
            background: "var(--bg-sidebar)",
            borderRight: "0.5px solid var(--border)",
          }}
        >
          {/* Collapse toggle */}
          <button
            onClick={() => setSidebarOpen((prev) => !prev)}
            className="absolute right-2 top-2 flex h-9 w-9 items-center justify-center rounded-lg text-sm transition"
            style={{
              background: "var(--bg-surface)",
              border: "0.5px solid var(--border-strong)",
              color: "var(--text-secondary)",
            }}
            title={sidebarOpen ? "Ẩn sidebar" : "Hiện sidebar"}
          >
            {sidebarOpen ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}
          </button>

          {/* Collapsed sidebar */}
          {!sidebarOpen && (
            <div className="flex flex-col items-center gap-3 mt-2">
              <div className="h-9 w-9" />
              <button
                onClick={createChat}
                className="flex h-9 w-9 items-center justify-center rounded-lg"
                style={{ background: "var(--bg-surface)", color: "var(--text-secondary)" }}
                title="Đoạn chat mới"
              >
                <Plus size={18} />
              </button>
              <button
                onClick={() => setSidebarOpen(true)}
                className="flex h-9 w-9 items-center justify-center rounded-lg"
                style={{ background: "var(--bg-surface)", color: "var(--text-secondary)" }}
                title="Tìm kiếm"
              >
                <Search size={18} />
              </button>
              <div className="mt-2 flex flex-col gap-2">
                {chats.slice(0, 5).map((chat) => (
                  <button
                    key={chat._id}
                    onClick={() => chatActions.setActiveChat(chat._id)}
                    className="h-9 w-9 rounded-lg text-xs flex items-center justify-center"
                    style={{ background: activeId === chat._id ? "var(--bg-msgbtn)" : "var(--bg-surface)" }}
                    title={chat.title ?? "Chat"}
                  >
                    <MessageCircle size={16} />
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Expanded sidebar */}
          {sidebarOpen && (
            <>
              <div>
                {/* Title + theme toggle */}
                <div className="mb-3 flex items-center justify-between pr-10">
                  <div className="flex items-center gap-2 text-lg font-semibold" style={{ color: "var(--text-primary)" }}>
                    <Image
                      src="/logo.png"
                      alt="VNLaw Logo"
                      width={65}
                      height={65}
                      className="rounded"
                    />
                    <span>Chatbot VNLaw</span>
                  </div>
                  <div className="flex items-center gap-2">
                    {/* Sun icon */}
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 24 24"
                      className="h-4 w-4"
                      style={{ color: !darkMode ? "#f59e0b" : "var(--text-muted)" }}
                    >
                      <path
                        fill="currentColor"
                        d="M6.76 4.84l-1.8-1.79-1.41 1.41 1.79 1.8 1.42-1.42zm10.45-1.79l-1.79 1.79 1.41 1.42 1.8-1.8-1.42-1.41zM12 4V1h-1v3h1zm0 19v-3h-1v3h1zm8-11h3v-1h-3v1zM4 12H1v-1h3v1zm12.24 7.16l1.8 1.79 1.41-1.41-1.79-1.8-1.42 1.42zM4.22 19.54l1.79-1.8-1.41-1.41-1.8 1.79 1.42 1.42zM12 6a6 6 0 100 12 6 6 0 000-12z"
                      />
                    </svg>

                    {/* Switch */}
                    <button
                      onClick={() => setDarkMode((p) => !p)}
                      className="relative inline-flex h-6 w-11 items-center rounded-full transition-all duration-300"
                      style={{
                        background: darkMode ? "#534AB7" : "var(--bg-input)",
                        border: "0.5px solid var(--border)",
                      }}
                    >
                      <span
                        className="inline-block h-4 w-4 transform rounded-full bg-white shadow transition-all duration-300"
                        style={{
                          transform: darkMode ? "translateX(20px)" : "translateX(4px)",
                        }}
                      />
                    </button>

                    {/* Moon icon */}
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 24 24"
                      className="h-4 w-4"
                      style={{ color: darkMode ? "#6366f1" : "var(--text-muted)" }}
                    >
                      <path
                        fill="currentColor"
                        d="M21 12.79A9 9 0 0111.21 3c0-.34.02-.67.05-1A9 9 0 1021 12.79z"
                      />
                    </svg>
                  </div>
                </div>

                <button
                  className="mb-3 w-full rounded-lg px-3 py-2 text-left text-sm transition"
                  style={{ background: "var(--bg-surface)", color: "var(--text-secondary)", border: "0.5px solid var(--border)" }}
                  onClick={createChat}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "var(--bg-surface)")}
                >
                  <div className="flex items-center gap-2">
                    <Plus size={16} />
                    Đoạn chat mới
                  </div>
                </button>

                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Tìm kiếm đoạn chat"
                  className="mb-3 w-full rounded-lg px-3 py-2 text-sm outline-none"
                  style={{
                    background: "var(--bg-input)",
                    border: "0.5px solid var(--border)",
                    color: "var(--text-primary)",
                  }}
                />

                {error && (
                  <div
                    className="mb-3 rounded-lg px-3 py-2 text-xs"
                    style={{
                      background: "var(--danger-bg)",
                      border: "0.5px solid var(--danger-border)",
                      color: "var(--danger-text)",
                    }}
                  >
                    {error}
                  </div>
                )}
              </div>

              <div className="space-y-1 overflow-y-auto pr-1" style={{ height: "calc(100vh - 220px)", overflow: "visible" }}>
                {/* Pinned */}
                {pinnedChats.length > 0 && (
                  <>
                    <div className="flex items-center gap-1 mb-1 px-2">
                      <span className="text-xs font-medium uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
                        <div className="flex items-center gap-1">
                          <Pin size={14} />
                          <span>Đã ghim</span>
                        </div>
                      </span>
                    </div>
                    {pinnedChats.map((chat) => <ChatItem key={chat._id} chat={chat} />)}
                    {unpinnedChats.length > 0 && (
                      <div className="my-2" style={{ borderTop: "0.5px solid var(--border)" }} />
                    )}
                  </>
                )}

                {/* Recent */}
                {unpinnedChats.length > 0 && (
                  <>
                    <div className="flex items-center gap-1 mb-1 px-2">
                      <span className="text-xs font-medium uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
                        <div className="flex items-center gap-1">
                          <Clock size={14} />
                          <span>Gần đây</span>
                        </div>
                      </span>
                    </div>
                    {unpinnedChats.map((chat) => <ChatItem key={chat._id} chat={chat} />)}
                  </>
                )}
              </div>
            </>
          )}
        </aside>

        {/* ── Main chat area ── */}
        <main
          className="flex flex-1 flex-col"
          style={{ background: "var(--bg-main)" }}
        >
          {/* Topbar */}
          <div
            className="px-6 py-4 text-base font-semibold"
            style={{
              borderBottom: "0.5px solid var(--border)",
              color: "var(--text-primary)",
              background: "var(--bg-main)",
            }}
          >
            {activeChat?.title ?? "Hệ thống hỏi đáp pháp luật Việt Nam"}
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-6 py-4">
            {activeChat?.messages?.length ? (
              <div className="space-y-4">
                {activeChat.messages.map((m, idx) => (
                  <MessageRow key={idx} m={m} />
                ))}

                {/* Streaming bubble */}
                {streamingChatId === activeChat._id && streamingText && (
                  <div className="flex items-end gap-2">
                    <Avatar type="bot" />
                    <BotBubble content={streamingText} />
                  </div>
                )}

                {/* Pending status bubble */}
                {pendingChatId === activeChat._id && pendingStatus && (
                  <div className="flex items-end gap-2">
                    <Avatar type="bot" />
                    <div
                      className="rounded-2xl rounded-bl-md px-4 py-3 text-sm"
                      style={{
                        background: "var(--status-bg)",
                        border: "0.5px solid var(--status-border)",
                        color: "var(--status-text)",
                      }}
                    >
                      <span className="animate-pulse">{pendingStatus}</span>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="mt-24 text-center text-sm" style={{ color: "var(--text-muted)" }}>
                Bắt đầu một đoạn chat mới...
              </div>
            )}
          </div>

          {/* Input */}
          <form onSubmit={onSend} className="p-4" style={{ borderTop: "0.5px solid var(--border)" }}>

          {/* ── Mode toggle ── */}
          <div className="mx-auto mb-2 flex items-center gap-2">
            <button
              type="button"
              onClick={() => setQueryMode((m) => m === "normal" ? "situation" : "normal")}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 12px",
                borderRadius: 20,
                fontSize: 12,
                fontWeight: 500,
                border: queryMode === "situation"
                  ? "1.5px solid #534AB7"
                  : "0.5px solid var(--border)",
                background: queryMode === "situation" ? "#EEEDFE" : "transparent",
                color: queryMode === "situation" ? "#3C3489" : "var(--text-muted)",
                cursor: "pointer",
                transition: "all 0.15s",
              }}
            >
              {/* dot indicator */}
              <span style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: queryMode === "situation" ? "#534AB7" : "var(--border-strong)",
                flexShrink: 0,
                transition: "background 0.15s",
              }} />
              {queryMode === "situation" ? "Phân tích tình huống" : "Hỏi đáp thường"}
            </button>

            {queryMode === "situation" && (
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                Mô tả đầy đủ: ai · làm gì · hoàn cảnh · hậu quả
              </span>
            )}
          </div>

          {/* ── Input row (giữ nguyên) ── */}
          <div className="mx-auto flex gap-3">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                queryMode === "situation"
                  ? "Mô tả tình huống: ai làm gì, với ai, hoàn cảnh, hậu quả..."
                  : "Nhập câu hỏi..."
              }
              className="flex-1 rounded-xl px-4 py-3 text-sm outline-none transition"
              style={{
                background: "var(--bg-input)",
                border: queryMode === "situation"
                  ? "0.5px solid #534AB7"
                  : "0.5px solid var(--border)",
                color: "var(--text-primary)",
              }}
              onFocus={(e) => (e.currentTarget.style.border =
                queryMode === "situation" ? "1px solid #534AB7" : "0.5px solid var(--accent)")}
              onBlur={(e) => (e.currentTarget.style.border =
                queryMode === "situation" ? "0.5px solid #534AB7" : "0.5px solid var(--border)")}
            />
            <button
              type="submit"
              disabled={loading || sending}
              className="rounded-xl px-5 py-3 text-sm font-medium transition disabled:opacity-60"
              style={{ background: "#534AB7", color: "var(--text-on-accent)", minWidth: 60 }}
              onMouseEnter={(e) => { if (!loading && !sending) (e.currentTarget as HTMLElement).style.background = "var(--accent-hover)"; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = "var(--accent)"; }}
            >
              {sending ? `${loadingDots}` : "Gửi"}
            </button>
          </div>
          </form>
        </main>
      </div>

      {/* ── Rename modal ── */}
      {renameChatId && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: "rgba(0,0,0,0.5)" }}
          onClick={(e) => { if (e.target === e.currentTarget) setRenameChatId(null); }}
        >
          <div
            className="w-[400px] rounded-xl p-4 shadow-xl"
            style={{ background: "var(--bg-surface)", border: "0.5px solid var(--border-strong)" }}
          >
            <div className="mb-3 text-base font-semibold" style={{ color: "var(--text-primary)" }}>
              Đổi tên cuộc trò chuyện
            </div>
            <input
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleRename(); } }}
              className="w-full rounded-lg px-3 py-2 text-sm outline-none"
              style={{
                background: "var(--bg-input)",
                border: "0.5px solid var(--border)",
                color: "var(--text-primary)",
              }}
              placeholder="Nhập tên mới..."
              autoFocus
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => { setRenameChatId(null); setRenameValue(""); }}
                className="rounded-lg px-3 py-2 text-sm transition"
                style={{ background: "var(--bg-hover)", color: "var(--text-secondary)" }}
              >
                Hủy
              </button>
              <button
                onClick={handleRename}
                className="rounded-lg px-3 py-2 text-sm font-medium transition"
                style={{ background: "var(--accent)", color: "var(--text-on-accent)" }}
              >
                Lưu
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────
   Sub-components
───────────────────────────────────────────── */
function Avatar({ type }: { type: "bot" | "user" }) {
  const src = type === "bot" ? BOT_AVATAR_SRC : USER_AVATAR_SRC;
  return (
    <Image
      src={src}
      alt={type}
      width={32}
      height={32}
      className="h-8 w-8 shrink-0 rounded-full object-cover"
    />
  );
}

function BotBubble({ content }: { content: string }) {
  return (
    <div
      className="w-fit max-w-[min(82%,760px)] rounded-2xl rounded-bl-md px-4 py-3 text-sm leading-7 shadow-sm"
      style={{
        background: "var(--bg-msg-bot)",
        border: "0.5px solid var(--border)",
        color: "var(--text-primary)",
      }}
    >
      <div className="[&_a]:underline [&_code]:rounded [&_code]:px-1 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:p-3 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-1 [&_p]:my-2 [&_blockquote]:border-l-4 [&_blockquote]:pl-3"
        style={{ ["--tw-prose-a" as string]: "var(--accent)" }}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
          {content}
        </ReactMarkdown>
      </div>
    </div>
  );
}

function MessageRow({ m }: { m: Message }) {
  const isUser = m.role === "user";
  return (
    <div className={`flex items-end gap-2 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && <Avatar type="bot" />}
      {isUser ? (
        <div
          className="w-fit max-w-[min(82%,760px)] rounded-2xl rounded-br-md px-4 py-3 text-sm leading-7"
          style={{ background: "#534AB7" , color: "#ffffff" }}
        >
          <div className="whitespace-pre-wrap break-words">{m.content}</div>
        </div>
      ) : (
        <BotBubble content={m.content} />
      )}
      {isUser && <Avatar type="user" />}
    </div>
  );
}

/* ─────────────────────────────────────────────
   Helpers
───────────────────────────────────────────── */
function autoTitle(messages: Message[], currentTitle: string) {
  if (!currentTitle.startsWith("Cuộc trò chuyện")) return currentTitle;
  const firstUser = messages.find((m) => m.role === "user")?.content?.trim();
  if (!firstUser) return currentTitle;
  return firstUser.length > 40 ? `${firstUser.slice(0, 40)}...` : firstUser;
}

function toSvgDataUri(svg: string) {
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}