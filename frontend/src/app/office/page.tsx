"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState, useSyncExternalStore } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowLeft, ArrowUp, BookOpen, ChevronDown, CircleHelp, Crosshair, Eye, FilePlus, FileText,
  FolderOpen, Globe, Hand, Map as MapIcon, MessageSquare, Moon, PersonStanding, Pin, RotateCcw,
  Scale, Sparkles, Square, SquarePen, Sun, Tag, X, Zap,
} from "lucide-react";
import {
  director,
  useDialog,
  useFocus,
  useOffice,
  usePovId,
  useViewMode,
  type Passage,
  type ViewMode,
} from "@/components/office/director";
import { CHARACTER_BY_ID, STAFF } from "@/components/office/officeData";
import { TouchControls } from "@/components/office/TouchControls";
import { useOfficeChat } from "@/components/office/useOfficeChat";
import { citedPassages } from "@/components/chat/MessageRow";
import type { Passage as ChatPassage } from "@/store/chat-store";
import { useThemeMode } from "@/hooks/useThemeMode";
import "./office.css";

const OfficeScene = dynamic(() => import("@/components/office/OfficeScene"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center text-sm" style={{ color: "var(--text-muted)" }}>
      Đang dựng văn phòng…
    </div>
  ),
});

/** Câu hỏi mẫu — cùng tinh thần với màn hình trống của trang chat */
const SUGGESTIONS = [
  { topic: "Hôn nhân và gia đình", q: "Điều kiện để được ly hôn đơn phương là gì?" },
  { topic: "Lao động", q: "Tôi bị công ty nợ lương 3 tháng thì phải làm sao?" },
  { topic: "Giao thông", q: "Mức phạt khi không đội mũ bảo hiểm?" },
  { topic: "Hộ tịch", q: "Thủ tục đăng ký kết hôn với người nước ngoài?" },
];

const VIEW_MODES: { mode: ViewMode; label: string; Icon: typeof MapIcon }[] = [
  { mode: "orbit", label: "Toàn cảnh", Icon: MapIcon },
  { mode: "fp", label: "Nhập vai", Icon: PersonStanding },
  { mode: "pov", label: "Mắt nhân vật", Icon: Eye },
];

function useMedia(query: string) {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(query);
      m.addEventListener("change", cb);
      return () => m.removeEventListener("change", cb);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

const fmtTime = (t: number) => new Date(t).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** Nút icon trong thanh công cụ — cùng kiểu với header trang chat */
function ToolButton({
  label, onClick, active, children, disabled,
}: { label: string; onClick: () => void; active?: boolean; children: React.ReactNode; disabled?: boolean }) {
  return (
    <button
      type="button" onClick={onClick} title={label} aria-label={label} aria-pressed={active} disabled={disabled}
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] disabled:opacity-50"
      style={active
        ? { background: "var(--bg-hover)", color: "var(--text-primary)" }
        : { color: "var(--text-secondary)" }}
    >
      {children}
    </button>
  );
}

/** Nút chọn nhiều lựa chọn (giống "Hỏi đáp / Phân tích tình huống") */
function Segmented<T extends string>({
  value, onChange, options, label, disabled,
}: {
  value: T; onChange: (v: T) => void; label: string; disabled?: boolean;
  options: { value: T; label: string; icon?: React.ReactNode; title?: string; hideLabelBelow?: "md" | "lg" }[];
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex shrink-0 rounded-lg p-0.5" style={{ background: "var(--bg-hover)" }}>
      {options.map((o) => (
        <button
          key={o.value} type="button" role="radio" aria-checked={value === o.value} title={o.title ?? o.label}
          disabled={disabled} onClick={() => onChange(o.value)}
          className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[13px] transition-colors disabled:cursor-not-allowed disabled:opacity-60"
          style={value === o.value
            ? { background: "var(--bg-surface)", color: "var(--text-primary)", boxShadow: "var(--shadow-sm)", fontWeight: 500 }
            : { color: "var(--text-secondary)" }}
        >
          {o.icon}
          <span className={o.hideLabelBelow === "lg" ? "hidden lg:inline" : o.hideLabelBelow === "md" ? "hidden md:inline" : ""}>
            {o.label}
          </span>
        </button>
      ))}
    </div>
  );
}

function PassagesModal({ passages, onClose }: { passages: Passage[]; onClose: () => void }) {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-6" onClick={onClose}
      role="dialog" aria-modal="true" aria-label="Tài liệu đã tra cứu">
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-t-2xl shadow-2xl sm:rounded-2xl"
        style={{ background: "var(--bg-surface)", color: "var(--text-primary)", border: "1px solid var(--border)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-2 font-semibold">
            <BookOpen size={18} style={{ color: "var(--accent-text)" }} aria-hidden />
            Tài liệu trên bàn luật sư ({passages.length})
          </div>
          <ToolButton label="Đóng" onClick={onClose}><X size={18} /></ToolButton>
        </div>
        <div className="space-y-2 overflow-y-auto p-3">
          {passages.map((p, i) => (
            <div key={`${p.mapc}-${i}`} className="rounded-lg" style={{ border: "1px solid var(--border)" }}>
              <button className="flex w-full items-start gap-2.5 px-3 py-2.5 text-left" onClick={() => setOpen(open === i ? null : i)}
                aria-expanded={open === i}>
                {p.source === "web"
                  ? <Globe size={15} className="mt-0.5 shrink-0" style={{ color: "var(--text-muted)" }} aria-hidden />
                  : <FileText size={15} className="mt-0.5 shrink-0" style={{ color: "var(--accent-text)" }} aria-hidden />}
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">{p.ten || p.mapc}</span>
                  <span className="block truncate text-xs" style={{ color: "var(--text-muted)" }}>
                    {[p.tenchude, p.tendemuc, p.tenchuong].filter(Boolean).join(" › ") || p.url || p.mapc}
                  </span>
                </span>
                <ChevronDown size={16} className={`mt-0.5 shrink-0 transition-transform ${open === i ? "rotate-180" : ""}`}
                  style={{ color: "var(--text-muted)" }} aria-hidden />
              </button>
              {open === i && (
                <div className="answer-prose office-answer whitespace-pre-wrap px-3 py-2.5" style={{ borderTop: "1px solid var(--border)" }}>
                  {p.noidung}
                  {p.url && (
                    <a href={p.url} target="_blank" rel="noreferrer" className="mt-2 block break-all text-xs">
                      {p.url}
                    </a>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function CharacterDot({ color, busy, size = 10 }: { color: string; busy?: boolean; size?: number }) {
  return (
    <span className={`shrink-0 rounded-full ${busy ? "animate-pulse" : ""}`}
      style={{ width: size, height: size, background: color, boxShadow: "0 0 0 1px var(--border-strong)" }} aria-hidden />
  );
}

function CharacterCard() {
  const { selected, chars } = useOffice();
  if (!selected) return null;
  const def = CHARACTER_BY_ID[selected];
  const view = chars[selected];
  return (
    <div className="office-panel pointer-events-auto w-[300px] max-w-[calc(100vw-32px)] rounded-xl p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-3">
          <CharacterDot color={def.top} size={36} />
          <div>
            <div className="font-semibold">{def.name}</div>
            <div className="text-xs" style={{ color: "var(--text-secondary)" }}>{def.role}</div>
          </div>
        </div>
        <ToolButton label="Đóng thẻ nhân vật" onClick={() => director.select(null)}><X size={16} /></ToolButton>
      </div>
      <p className="mt-2 text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>{def.description}</p>
      <div className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>Phụ trách: <code>{def.backend}</code></div>
      <div className="mt-3 flex items-center gap-2 text-xs" style={{ color: "var(--text-secondary)" }}>
        <span className={`h-2 w-2 rounded-full ${view.busy ? "animate-pulse" : ""}`}
          style={{ background: view.busy ? "var(--ok-text)" : "var(--border-strong)" }} aria-hidden />
        <span>{view.activity}</span>
      </div>
      {selected !== "client" && (
        <div className="mt-3 flex flex-wrap gap-2">
          <button className="office-btn" onClick={() => director.openDialog(selected)}>
            <MessageSquare size={14} aria-hidden />Nói chuyện
          </button>
          <button className="office-btn" onClick={() => { director.setView("pov", selected); director.select(null); }}>
            <Eye size={14} aria-hidden />Nhìn bằng mắt {def.name}
          </button>
        </div>
      )}
      {view.log.length > 0 && (
        <div className="mt-3 max-h-36 space-y-1 overflow-y-auto pt-2 text-xs" style={{ borderTop: "1px solid var(--border)" }}>
          {[...view.log].reverse().map((l, i) => (
            <div key={i} className="flex gap-2">
              <span className="shrink-0 tabular-nums" style={{ color: "var(--text-muted)" }}>{fmtTime(l.t)}</span>
              <span style={{ color: "var(--text-secondary)" }}>{l.text}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

type CaseItem = { id: string; title: string; count: number; updated?: string; pinned: boolean };

/** Nhân viên có thể mở hồ sơ cũ / vụ việc mới */
const CASE_KEEPERS = ["memory", "receptionist"];

/** Hộp hội thoại khi nói chuyện với một nhân viên */
function DialogBox({
  onAsk, onShowDocs, hasDocs, initialStage, cases, activeCaseId, live, busy, onOpenCase, onNewCase,
}: {
  onAsk: (q: string) => void;
  onShowDocs: () => void;
  hasDocs: boolean;
  initialStage: "menu" | "cases";
  cases: CaseItem[];
  activeCaseId: string | null;
  live: boolean;
  busy: boolean;
  onOpenCase: (id: string) => void;
  onNewCase: () => void;
}) {
  const who = useDialog();
  const { chars } = useOffice();
  const [stage, setStage] = useState<"menu" | "about" | "ask" | "cases">(initialStage);
  const [q, setQ] = useState("");
  if (!who) return null;
  const isKeeper = CASE_KEEPERS.includes(who);
  const pickCase = (c: CaseItem) => {
    director.closeDialog(true);
    onOpenCase(c.id);
    // quản lý hồ sơ đi lấy tập hồ sơ mang tới cho khách
    if (who !== "memory") director.npcSay(who, "Để chị Hạnh lấy hồ sơ cho anh/chị nhé!", 3000);
    director.fetchCase(c.title);
  };
  const newCase = () => {
    director.closeDialog(true);
    onNewCase();
    director.newCase(who);
  };
  const def = CHARACTER_BY_ID[who];
  const view = chars[who];
  const ask = () => {
    if (!q.trim()) return;
    director.npcSay(who, who === "lead" ? "Được, để tôi xem xét ngay." : "Văn phòng sẽ xử lý ngay, mời anh/chị chờ chút!", 3000);
    director.closeDialog();
    onAsk(q.trim());
  };
  const canShowDocs = hasDocs && ["lead", "librarian", "archivist", "clerk", "researcher"].includes(who);
  return (
    <div className="office-panel pointer-events-auto w-[min(560px,calc(100vw-24px))] rounded-xl p-4" role="dialog" aria-label={`Nói chuyện với ${def.name}`}>
      <div className="flex items-center gap-3">
        <CharacterDot color={def.top} size={40} />
        <div className="min-w-0 flex-1">
          <div className="font-semibold">
            {def.name} <span className="text-xs font-normal" style={{ color: "var(--text-muted)" }}>{def.role}</span>
          </div>
          <div className="truncate text-xs" style={{ color: "var(--text-muted)" }}>Đang: {view.activity}</div>
        </div>
        <ToolButton label="Kết thúc trò chuyện" onClick={() => director.closeDialog()}><X size={18} /></ToolButton>
      </div>
      <div className="mt-3 rounded-lg p-3 text-[15px] leading-relaxed" style={{ background: "var(--bg-hover)" }}>
        {stage === "about" ? (
          <>
            {def.description}
            <div className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>Phụ trách: <code>{def.backend}</code></div>
          </>
        ) : stage === "ask" ? (
          "Anh/chị cứ trình bày vấn đề, tôi sẽ chuyển cho cả văn phòng cùng xử lý."
        ) : stage === "cases" ? (
          !live ? (
            "Tủ hồ sơ chỉ lưu vụ việc thật. Anh/chị chuyển sang “Dữ liệu thật” ở thanh trên để tôi tra nhé."
          ) : cases.length === 0 ? (
            "Anh/chị chưa có hồ sơ nào ở văn phòng. Mình mở vụ việc mới nhé?"
          ) : (
            <>
              Đây là các hồ sơ của anh/chị, muốn xem tiếp vụ nào ạ?
              <div className="mt-2 max-h-48 space-y-1 overflow-y-auto pr-1">
                {cases.map((c) => (
                  <button key={c.id} disabled={busy} onClick={() => pickCase(c)}
                    className="office-option w-full" style={c.id === activeCaseId ? { background: "var(--bg-surface)" } : undefined}>
                    {c.pinned ? <Pin size={14} className="shrink-0" aria-hidden /> : <FolderOpen size={14} className="shrink-0" aria-hidden />}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{c.title}</span>
                      <span className="block text-xs" style={{ color: "var(--text-muted)" }}>
                        {c.count} tin nhắn{c.updated ? `, ${new Date(c.updated).toLocaleString("vi-VN")}` : ""}
                        {c.id === activeCaseId ? " (đang mở)" : ""}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </>
          )
        ) : (
          def.greet
        )}
      </div>
      {stage === "ask" ? (
        <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); ask(); }}>
          <input
            autoFocus value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Ví dụ: Tôi bị công ty nợ lương 3 tháng…" aria-label="Câu hỏi của bạn"
            className="min-w-0 flex-1 rounded-lg px-3 py-2 text-sm outline-none placeholder:text-[var(--text-muted)]"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border-strong)", color: "var(--text-primary)" }}
          />
          <button className="rounded-lg px-4 text-sm font-medium transition-colors enabled:hover:bg-[var(--accent-hover)] disabled:opacity-40"
            style={{ background: "var(--accent)", color: "var(--text-on-accent)" }} disabled={!q.trim()}>
            Gửi câu hỏi
          </button>
        </form>
      ) : stage === "cases" ? (
        <div className="mt-3 grid gap-1.5 sm:grid-cols-2">
          {live && (
            <button className="office-option" disabled={busy} onClick={newCase}><FilePlus size={15} aria-hidden />Mở vụ việc mới</button>
          )}
          <button className="office-option" onClick={() => setStage("menu")}><ArrowLeft size={15} aria-hidden />Quay lại</button>
        </div>
      ) : (
        <div className="mt-3 grid gap-1.5 sm:grid-cols-2">
          <button className="office-option" onClick={() => setStage("ask")}><Scale size={15} aria-hidden />Tôi muốn hỏi một vấn đề pháp lý</button>
          {isKeeper && (
            <button className="office-option" onClick={() => setStage("cases")}><FolderOpen size={15} aria-hidden />Cho tôi xem lại hồ sơ cũ</button>
          )}
          {isKeeper && live && activeCaseId && (
            <button className="office-option" disabled={busy} onClick={newCase}><FilePlus size={15} aria-hidden />Tôi có vụ việc mới</button>
          )}
          <button className="office-option" onClick={() => setStage("about")}><CircleHelp size={15} aria-hidden />Anh/chị phụ trách việc gì?</button>
          {canShowDocs && (
            <button className="office-option" onClick={onShowDocs}><BookOpen size={15} aria-hidden />Cho tôi xem tài liệu vừa tìm</button>
          )}
          <button className="office-option" onClick={() => director.closeDialog()}><Hand size={15} aria-hidden />Tạm biệt</button>
        </div>
      )}
    </div>
  );
}

/** HUD góc nhìn thứ nhất: tâm ngắm, gợi ý tương tác, hướng dẫn điều khiển */
function FirstPersonHud({ touch, busy }: { touch: boolean; busy: boolean }) {
  const focus = useFocus();
  const dialog = useDialog();
  if (dialog) return null;
  const def = focus ? CHARACTER_BY_ID[focus] : null;
  return (
    <>
      <div className="pointer-events-none absolute left-1/2 top-1/2 z-20 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white/90 ring-2 ring-black/30" />
      {def && (
        <div className="absolute left-1/2 top-[58%] z-30 -translate-x-1/2">
          <button className="office-panel pointer-events-auto flex items-center gap-2 rounded-full px-4 py-2 text-sm"
            onClick={() => director.openDialog(def.id)}>
            {!touch && (
              <kbd className="rounded px-1.5 text-xs" style={{ border: "1px solid var(--border-strong)" }}>E</kbd>
            )}
            Nói chuyện với <b>{def.name}</b>
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>{def.role}</span>
          </button>
        </div>
      )}
      {!touch && !busy && (
        <div className="office-panel pointer-events-none absolute bottom-4 left-1/2 z-20 -translate-x-1/2 whitespace-nowrap rounded-full px-4 py-1.5 text-xs"
          style={{ color: "var(--text-secondary)" }}>
          Bấm vào cảnh để điều khiển, WASD để đi, Shift để chạy, E để nói chuyện, Esc để nhả chuột
        </div>
      )}
    </>
  );
}

/** Nói chuyện với bất kỳ nhân viên nào mà không cần đi tới */
function TalkToBar() {
  const dialog = useDialog();
  const { chars } = useOffice();
  return (
    <div className="office-panel pointer-events-auto flex max-w-[calc(100vw-24px)] items-center gap-0.5 overflow-x-auto rounded-xl p-1 lg:max-w-[calc(100vw-448px)]"
      role="toolbar" aria-label="Nói chuyện với nhân viên">
      <span className="shrink-0 px-2 text-xs" style={{ color: "var(--text-muted)" }}>Nói chuyện với</span>
      {STAFF.map((c) => (
        <button
          key={c.id}
          className={`office-chip flex shrink-0 items-center gap-1.5 ${dialog === c.id ? "is-on" : ""}`}
          title={`${c.role}: ${chars[c.id].activity}`}
          onClick={() => {
            director.select(null);
            if (dialog === c.id) director.closeDialog();
            else director.openDialog(c.id, "menu", true);
          }}
        >
          <CharacterDot color={c.top} busy={chars[c.id].busy} />
          {c.name}
          <span className="hidden xl:inline" style={{ color: "var(--text-muted)" }}>{c.role}</span>
        </button>
      ))}
    </div>
  );
}

/** Chọn nhân vật để nhìn bằng mắt họ */
function PovBar() {
  const povId = usePovId();
  return (
    <div className="office-panel pointer-events-auto flex max-w-[calc(100vw-24px)] gap-0.5 overflow-x-auto rounded-xl p-1"
      role="toolbar" aria-label="Nhìn bằng mắt nhân vật">
      {STAFF.map((c) => (
        <button key={c.id} className={`office-chip flex items-center gap-1.5 ${povId === c.id ? "is-on" : ""}`}
          onClick={() => director.setView("pov", c.id)}>
          <CharacterDot color={c.top} />
          {c.name}
        </button>
      ))}
    </div>
  );
}

export default function OfficePageRoute() {
  return (
    <Suspense>
      <OfficePage />
    </Suspense>
  );
}

function OfficePage() {
  // Mặc định dữ liệu MẪU (không cần backend). Dữ liệu thật khi ?live=1, ?chat=<id> (mở hội thoại
  // thật từ trang chat) hoặc NEXT_PUBLIC_OFFICE_DATA=live
  const searchParams = useSearchParams();
  const initialChatId = searchParams.get("chat");
  const [mock, setMock] = useState(
    searchParams.get("live") !== "1" && !initialChatId && process.env.NEXT_PUBLIC_OFFICE_DATA !== "live",
  );
  const { messages, busy, status, error, send, stop, reset, chats, activeChat, chatsLoading, openChat } = useOfficeChat(
    mock,
    initialChatId,
  );
  const { passages, timeline, iteration } = useOffice();
  const [darkMode, toggleDarkMode] = useThemeMode();
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<"normal" | "situation">("normal");
  const [tab, setTab] = useState<"chat" | "history" | "activity">("chat");
  const [docsOpen, setDocsOpen] = useState<Passage[] | null>(null);
  const [follow, setFollow] = useState(false);
  const [signs, setSigns] = useState(true);
  const [resetToken, setResetToken] = useState(0);
  const [sheetOpen, setSheetOpen] = useState(true);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const viewMode = useViewMode();
  const povId = usePovId();
  const dialog = useDialog();
  const touch = useMedia("(pointer: coarse)");
  const [qualityPick, setQualityPick] = useState<"high" | "low" | null>(null);
  const quality = qualityPick ?? (touch ? "low" : "high");
  const changeView = (m: ViewMode) => {
    director.setView(m);
    director.select(null);
    if (m !== "orbit" && window.matchMedia("(max-width: 1023px)").matches) setSheetOpen(false);
  };

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, timeline, tab]);

  // Ô nhập tự giãn: thu về một dòng khi đã gửi xong
  useEffect(() => {
    if (!input && inputRef.current) inputRef.current.style.height = "auto";
  }, [input]);

  const submit = (text = input) => {
    if (!text.trim() || busy) return;
    setInput("");
    setTab("chat");
    // mobile: thu khung chat để xem văn phòng làm việc, mở lại khi có câu trả lời
    const isMobile = window.matchMedia("(max-width: 1023px)").matches;
    if (isMobile) setSheetOpen(false);
    send(text, mode).then(() => {
      if (isMobile) setSheetOpen(true);
    });
  };

  const tabs = (mock ? (["chat", "activity"] as const) : (["chat", "history", "activity"] as const)).map((t) => ({
    value: t,
    label: t === "chat" ? "Hội thoại" : t === "history" ? `Hồ sơ cũ (${chats.length})` : `Hoạt động (${timeline.length})`,
  }));

  return (
    <div className="office-root fixed inset-0 flex flex-col overflow-hidden">
      {/* ── Header: cùng chiều cao + kiểu nút với trang chat ── */}
      <header className="relative z-40 flex h-14 shrink-0 items-center gap-2 px-2 md:px-4"
        style={{ background: "var(--bg-main)", borderBottom: "1px solid var(--border)" }}>
        <Link href="/" title="Về giao diện chat"
          className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-sm transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
          style={{ color: "var(--text-secondary)" }}>
          <ArrowLeft size={16} aria-hidden /><span className="hidden sm:inline">Đoạn chat</span>
        </Link>
        <span className="hidden h-5 w-px md:block" style={{ background: "var(--border)" }} aria-hidden />
        <h1 className="hidden truncate text-[15px] font-medium md:block" style={{ color: "var(--text-primary)" }}>
          Văn phòng luật 3D
        </h1>
        <div className="mx-auto md:ml-4 md:mr-0">
          <Segmented
            label="Góc nhìn" value={viewMode} onChange={changeView}
            options={VIEW_MODES.map(({ mode: m, label, Icon }) => ({
              value: m, label, icon: <Icon size={15} aria-hidden />, hideLabelBelow: "lg" as const,
            }))}
          />
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-0.5">
          {viewMode === "orbit" && (
            <div className="hidden items-center gap-0.5 md:flex">
              <ToolButton label={signs ? "Ẩn biển khu vực" : "Hiện biển khu vực"} active={signs} onClick={() => setSigns((v) => !v)}>
                <Tag size={17} />
              </ToolButton>
              <ToolButton label="Camera bám theo nhân vật đang chọn" active={follow} onClick={() => setFollow((v) => !v)}>
                <Crosshair size={17} />
              </ToolButton>
              <ToolButton label="Về góc nhìn ban đầu" onClick={() => setResetToken((n) => n + 1)}>
                <RotateCcw size={17} />
              </ToolButton>
            </div>
          )}
          <ToolButton
            label={quality === "high" ? "Đồ hoạ đẹp (bấm để chuyển sang nhẹ)" : "Đồ hoạ nhẹ (bấm để chuyển sang đẹp)"}
            onClick={() => setQualityPick(quality === "high" ? "low" : "high")}
          >
            {quality === "high" ? <Sparkles size={17} /> : <Zap size={17} />}
          </ToolButton>
          <span className="mx-1 hidden h-5 w-px sm:block" style={{ background: "var(--border)" }} aria-hidden />
          <div>
            <Segmented
              label="Nguồn dữ liệu" disabled={busy} value={mock ? "mock" : "live"}
              onChange={(v) => { setMock(v === "mock"); setTab("chat"); }}
              options={[
                { value: "mock", label: "Mẫu", title: "Dữ liệu mẫu, không gọi backend" },
                { value: "live", label: "Thật", title: "Gọi backend thật (rag_api)" },
              ]}
            />
          </div>
          <ToolButton label={darkMode ? "Chuyển sang nền sáng" : "Chuyển sang nền tối"} onClick={toggleDarkMode}>
            {darkMode ? <Sun size={17} /> : <Moon size={17} />}
          </ToolButton>
        </div>
      </header>

      <div className="relative flex min-h-0 flex-1">
        {/* ── Cảnh 3D ── */}
        <main className="relative min-w-0 flex-1" aria-label="Văn phòng luật 3D">
          <div className={`absolute inset-x-0 top-0 lg:bottom-0 ${sheetOpen ? "bottom-[52vh]" : "bottom-[64px]"}`}>
            <OfficeScene
              onOpenDocs={() => setDocsOpen(passages)}
              follow={follow}
              showSigns={signs}
              resetToken={resetToken}
              quality={quality}
              dark={darkMode}
            />
            {viewMode === "fp" && touch && !dialog && <TouchControls />}
            {viewMode === "fp" && <FirstPersonHud touch={touch} busy={busy} />}
            {viewMode === "pov" && (
              <div className="office-panel pointer-events-none absolute bottom-4 left-1/2 z-20 -translate-x-1/2 whitespace-nowrap rounded-full px-4 py-1.5 text-xs"
                style={{ color: "var(--text-secondary)" }}>
                Đang nhìn qua mắt <b style={{ color: "var(--text-primary)" }}>{CHARACTER_BY_ID[povId].name}</b>
                {" "}({CHARACTER_BY_ID[povId].role}), kéo để nhìn quanh
              </div>
            )}

            {/* Thanh nói chuyện + thẻ nhân vật, nổi trên cảnh */}
            <div className="pointer-events-none absolute left-3 top-3 z-30 flex flex-col gap-2">
              <TalkToBar />
              {viewMode === "orbit" && !dialog && <CharacterCard />}
              {viewMode === "pov" && <PovBar />}
            </div>

            {busy && status && (
              <div className="pointer-events-none absolute bottom-4 left-1/2 z-30 -translate-x-1/2" role="status" aria-live="polite">
                <div className="office-panel flex max-w-[92vw] items-center gap-2.5 whitespace-nowrap rounded-full px-4 py-1.5 text-[13px]">
                  <span className="relative flex h-2 w-2 shrink-0" aria-hidden>
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60" style={{ background: "var(--accent)" }} />
                    <span className="relative inline-flex h-2 w-2 rounded-full" style={{ background: "var(--accent)" }} />
                  </span>
                  <span style={{ color: "var(--text-secondary)" }}>{status}</span>
                  {iteration && <span style={{ color: "var(--text-muted)" }}>bước {iteration.cur}/{iteration.max}</span>}
                </div>
              </div>
            )}

            {dialog && (
              <div className="absolute inset-x-0 bottom-3 z-40 flex justify-center px-3">
                <DialogBox
                  key={director.dialogSession}
                  initialStage={director.dialogStart}
                  hasDocs={passages.length > 0}
                  onShowDocs={() => setDocsOpen(passages)}
                  onAsk={(q) => submit(q)}
                  live={!mock}
                  busy={busy}
                  activeCaseId={activeChat?._id ?? null}
                  cases={chats.map((c) => ({
                    id: c._id,
                    title: c.title || "Cuộc trò chuyện mới",
                    count: c.messages?.length ?? 0,
                    updated: c.updated_at ?? c.updatedAt ?? c.created_at ?? c.createdAt,
                    pinned: !!c.pinned,
                  }))}
                  onOpenCase={(id) => { openChat(id); setTab("chat"); setSheetOpen(true); }}
                  onNewCase={() => { reset(); setTab("chat"); }}
                />
              </div>
            )}
          </div>
        </main>

        {/* ── Panel hội thoại: cột phải trên desktop, ngăn kéo dưới trên mobile ── */}
        <aside
          aria-label="Hội thoại với văn phòng"
          className={`absolute inset-x-0 bottom-0 z-30 flex flex-col rounded-t-2xl transition-[height] duration-300 lg:static lg:h-full lg:w-[420px] lg:shrink-0 lg:rounded-none ${
            sheetOpen ? "h-[52vh]" : "h-[64px]"
          }`}
          style={{ background: "var(--bg-main)", borderLeft: "1px solid var(--border)", borderTop: "1px solid var(--border)" }}
        >
          <button className="mx-auto mt-2 h-1.5 w-12 shrink-0 rounded-full lg:hidden" style={{ background: "var(--border-strong)" }}
            onClick={() => setSheetOpen((v) => !v)} aria-label={sheetOpen ? "Thu khung hội thoại" : "Mở khung hội thoại"} />
          <div className="flex shrink-0 items-center gap-2 px-4 pb-2 pt-2 lg:pt-3">
            <Segmented label="Mục" value={tab} onChange={(t) => { setTab(t); setSheetOpen(true); }} options={tabs} />
            <div className="flex-1" />
            {(mock ? messages.length > 0 : !!activeChat) && !busy && (
              <button
                className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-[13px] transition-colors hover:bg-[var(--bg-hover)]"
                style={{ color: "var(--text-secondary)" }}
                onClick={() => { reset(); setTab("chat"); }}
              >
                <SquarePen size={15} aria-hidden />Vụ việc mới
              </button>
            )}
          </div>
          {!mock && tab === "chat" && (
            <div className="flex shrink-0 items-center gap-1.5 truncate px-4 pb-2 text-xs" style={{ color: "var(--text-muted)" }}
              title={activeChat?.title}>
              <FolderOpen size={13} className="shrink-0" aria-hidden />
              <span className="truncate">{activeChat ? activeChat.title : "Vụ việc mới: câu hỏi đầu tiên sẽ tạo hồ sơ mới"}</span>
            </div>
          )}
          {error && (
            <div className="mx-4 mb-2 shrink-0 rounded-md px-3 py-2 text-xs"
              style={{ background: "var(--danger-bg)", border: "1px solid var(--danger-border)", color: "var(--danger-text)" }}>
              {error}
            </div>
          )}

          <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto px-4 pb-3" style={{ borderTop: "1px solid var(--border)" }}>
            {tab === "history" ? (
              chatsLoading && chats.length === 0 ? (
                <p className="pt-4 text-sm" style={{ color: "var(--text-muted)" }}>Đang tải hồ sơ…</p>
              ) : chats.length === 0 ? (
                <p className="pt-4 text-sm" style={{ color: "var(--text-muted)" }}>Chưa có hồ sơ nào.</p>
              ) : (
                <div className="space-y-0.5 pt-3">
                  {chats.map((c) => {
                    const isActive = c._id === activeChat?._id;
                    const updated = c.updated_at ?? c.updatedAt ?? c.created_at ?? c.createdAt;
                    return (
                      <button
                        key={c._id} disabled={busy} aria-current={isActive ? "page" : undefined}
                        className={`flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors disabled:opacity-50 ${isActive ? "" : "hover:bg-[var(--bg-hover)]"}`}
                        style={isActive ? { background: "var(--bg-surface)", boxShadow: "var(--shadow-sm)" } : undefined}
                        onClick={() => { openChat(c._id); setTab("chat"); }}
                      >
                        {c.pinned && <Pin size={13} className="shrink-0" style={{ color: "var(--text-muted)" }} aria-hidden />}
                        <span className="min-w-0 flex-1">
                          <span className="block truncate" style={{ fontWeight: isActive ? 500 : 400 }}>{c.title || "Cuộc trò chuyện mới"}</span>
                          <span className="block text-xs" style={{ color: "var(--text-muted)" }}>
                            {c.messages?.length ?? 0} tin nhắn{updated ? `, ${new Date(updated).toLocaleString("vi-VN")}` : ""}
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              )
            ) : tab === "chat" ? (
              messages.length === 0 ? (
                <section className="pt-5" aria-labelledby="office-empty-title">
                  <h2 id="office-empty-title" className="text-xl font-semibold tracking-tight">Văn phòng sẵn sàng tiếp bạn</h2>
                  <p className="mt-2 text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                    Trình bày vấn đề của bạn. Lễ tân, thư ký, luật sư và các trợ lý sẽ cùng xử lý ngay trong văn phòng bên cạnh.
                  </p>
                  <ul className="mt-5 overflow-hidden rounded-xl" style={{ border: "1px solid var(--border)", background: "var(--bg-surface)" }}>
                    {SUGGESTIONS.map((s, i) => (
                      <li key={s.q} style={i ? { borderTop: "1px solid var(--border)" } : undefined}>
                        <button type="button" disabled={busy} onClick={() => submit(s.q)}
                          className="flex w-full flex-col gap-0.5 px-4 py-3 text-left transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-60">
                          <span className="text-xs" style={{ color: "var(--text-muted)" }}>{s.topic}</span>
                          <span className="text-sm">{s.q}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : (
                <div className="space-y-6 pt-4">
                  {messages.map((m, i) =>
                    m.role === "user" ? (
                      <div key={i} className="flex justify-end">
                        <div className="w-fit max-w-[88%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md px-3.5 py-2 text-[15px] leading-relaxed"
                          style={{ background: "var(--bg-msg-user)" }}>
                          {m.content}
                        </div>
                      </div>
                    ) : (
                      <AnswerBlock key={i} content={m.content} passages={m.passages} cacheHit={m.cache_hit}
                        onOpenDocs={() => setDocsOpen(m.passages ?? [])} />
                    ),
                  )}
                  {busy && (
                    <div className="flex items-center gap-2 text-sm" style={{ color: "var(--text-secondary)" }}>
                      <span className="office-dots" aria-hidden /> Văn phòng đang xử lý…
                    </div>
                  )}
                </div>
              )
            ) : timeline.length === 0 ? (
              <p className="pt-4 text-sm" style={{ color: "var(--text-muted)" }}>Chưa có hoạt động nào.</p>
            ) : (
              <div className="space-y-0.5 pt-3 text-[13px]">
                {timeline.map((it, i) => {
                  const def = CHARACTER_BY_ID[it.who];
                  return (
                    <button key={i} className="flex w-full items-start gap-2 rounded-md px-2 py-1 text-left transition-colors hover:bg-[var(--bg-hover)]"
                      onClick={() => director.select(it.who)}>
                      <span className="shrink-0 tabular-nums" style={{ color: "var(--text-muted)" }}>{fmtTime(it.t)}</span>
                      <span className="flex shrink-0 items-center gap-1.5 font-medium">
                        <CharacterDot color={def.top} size={8} />{def.name}
                      </span>
                      <span style={{ color: "var(--text-secondary)" }}>{it.text}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* Ô nhập — giống trang chat */}
          <form className="shrink-0 px-4 pb-3 pt-2" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <div className="mb-2">
              <Segmented label="Kiểu câu hỏi" value={mode} onChange={setMode}
                options={[{ value: "normal", label: "Hỏi đáp" }, { value: "situation", label: "Phân tích tình huống" }]} />
            </div>
            <div className="flex items-end gap-2 rounded-2xl p-2 transition-shadow focus-within:shadow-[var(--focus-ring)]"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border-strong)", boxShadow: "var(--shadow-input)" }}>
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  e.target.style.height = "auto";
                  e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
                }}
                onFocus={() => setSheetOpen(true)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    submit();
                  }
                }}
                rows={1}
                aria-label="Câu hỏi của bạn"
                placeholder={mode === "situation" ? "Mô tả tình huống của bạn…" : "Trình bày câu hỏi với văn phòng…"}
                className="max-h-[160px] min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-[15px] leading-relaxed outline-none placeholder:text-[var(--text-muted)]"
                style={{ color: "var(--text-primary)", boxShadow: "none", minWidth: 0 }}
              />
              {busy ? (
                <button type="button" onClick={stop} title="Dừng xử lý" aria-label="Dừng xử lý"
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full"
                  style={{ background: "var(--text-primary)", color: "var(--bg-main)" }}>
                  <Square size={14} fill="currentColor" />
                </button>
              ) : (
                <button type="submit" disabled={!input.trim()} title="Gửi câu hỏi" aria-label="Gửi câu hỏi"
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition-colors enabled:hover:bg-[var(--accent-hover)] disabled:cursor-not-allowed"
                  style={input.trim()
                    ? { background: "var(--accent)", color: "var(--text-on-accent)" }
                    : { background: "var(--bg-hover)", color: "var(--text-muted)" }}>
                  <ArrowUp size={18} strokeWidth={2.25} />
                </button>
              )}
            </div>
            <p className="mt-2 text-center text-xs" style={{ color: "var(--text-muted)" }}>
              Thông tin chỉ để tham khảo, không thay thế tư vấn của luật sư.
            </p>
          </form>
        </aside>
      </div>

      {docsOpen && <PassagesModal passages={docsOpen} onClose={() => setDocsOpen(null)} />}
    </div>
  );
}

/** Câu trả lời của luật sư: cùng kiểu văn bản + thẻ căn cứ với trang chat */
function AnswerBlock({
  content, passages, cacheHit, onOpenDocs,
}: { content: string; passages?: Passage[]; cacheHit?: boolean; onOpenDocs: () => void }) {
  const list = passages ?? [];
  const cited = citedPassages(content, list as unknown as ChatPassage[]);
  const web = list.filter((p) => p.source === "web" && p.url);
  const host = (u?: string) => { try { return u ? new URL(u).hostname.replace(/^www\./, "") : ""; } catch { return ""; } };
  return (
    <article aria-label="Câu trả lời của luật sư">
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
        <Scale size={13} aria-hidden />LS. Hải{cacheHit ? ", trả lời đã lưu" : ""}
      </div>
      <div className="answer-prose office-answer min-w-0 break-words">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </div>
      {(cited.length > 0 || web.length > 0) && (
        <div className="mt-3">
          <div className="mb-1.5 text-xs font-medium" style={{ color: "var(--text-secondary)" }}>Căn cứ pháp lý</div>
          <div className="flex flex-wrap gap-1.5">
            {cited.slice(0, 4).map((c) => (
              <button key={c.label} type="button" onClick={onOpenDocs} title={c.title}
                className="inline-flex max-w-full items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors hover:brightness-95"
                style={{ background: "var(--accent-light)", border: "1px solid var(--accent)", color: "var(--accent-text)" }}>
                <BookOpen size={13} className="shrink-0" aria-hidden /><span className="truncate">{c.label}</span>
              </button>
            ))}
            {web.slice(0, 2).map((p) => (
              <a key={p.url} href={p.url} target="_blank" rel="noreferrer" title={p.ten}
                className="inline-flex max-w-full items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors hover:bg-[var(--bg-hover)]"
                style={{ border: "1px solid var(--border-strong)", color: "var(--text-secondary)" }}>
                <Globe size={13} className="shrink-0" aria-hidden /><span className="truncate">{host(p.url)}</span>
              </a>
            ))}
          </div>
        </div>
      )}
      {list.length > 0 && (
        <button type="button" onClick={onOpenDocs}
          className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs transition-colors hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-muted)" }}>
          <BookOpen size={14} aria-hidden />Xem {list.length} tài liệu đã tra
        </button>
      )}
    </article>
  );
}
