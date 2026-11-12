"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState, useSyncExternalStore } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
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
import "./office.css";

const OfficeScene = dynamic(() => import("@/components/office/OfficeScene"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center text-sm text-slate-400">Đang dựng văn phòng…</div>
  ),
});

const SUGGESTIONS = [
  "Điều kiện để được ly hôn đơn phương là gì?",
  "Tôi bị công ty nợ lương 3 tháng thì phải làm sao?",
  "Mức phạt khi không đội mũ bảo hiểm?",
  "Thủ tục đăng ký kết hôn với người nước ngoài?",
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

function PassagesModal({ passages, onClose }: { passages: Passage[]; onClose: () => void }) {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-6" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-t-2xl bg-white text-slate-800 shadow-2xl sm:rounded-2xl dark:bg-slate-900 dark:text-slate-100"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-700">
          <div className="font-semibold">📁 Hồ sơ trên bàn luật sư ({passages.length})</div>
          <button onClick={onClose} className="rounded-md px-2 py-1 text-sm hover:bg-slate-100 dark:hover:bg-slate-800">
            Đóng ✕
          </button>
        </div>
        <div className="overflow-y-auto p-3">
          {passages.map((p, i) => (
            <div key={`${p.mapc}-${i}`} className="mb-2 rounded-xl border border-slate-200 dark:border-slate-700">
              <button className="flex w-full items-start gap-2 px-3 py-2 text-left" onClick={() => setOpen(open === i ? null : i)}>
                <span className="mt-0.5 text-xs">{p.source === "web" ? "🌐" : "📜"}</span>
                <span className="flex-1">
                  <span className="block text-sm font-medium">{p.ten || p.mapc}</span>
                  <span className="block text-xs text-slate-500">
                    {[p.tenchude, p.tendemuc, p.tenchuong].filter(Boolean).join(" › ") || p.url || p.mapc}
                  </span>
                </span>
                <span className="text-xs text-slate-400">{open === i ? "▲" : "▼"}</span>
              </button>
              {open === i && (
                <div className="whitespace-pre-wrap border-t border-slate-200 px-3 py-2 text-sm leading-relaxed dark:border-slate-700">
                  {p.noidung}
                  {p.url && (
                    <a href={p.url} target="_blank" rel="noreferrer" className="mt-2 block text-xs text-sky-600 underline">
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

function CharacterCard() {
  const { selected, chars } = useOffice();
  if (!selected) return null;
  const def = CHARACTER_BY_ID[selected];
  const view = chars[selected];
  return (
    <div className="office-panel pointer-events-auto w-[300px] max-w-[calc(100vw-32px)] rounded-2xl p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-full ring-2 ring-white/30" style={{ background: def.top }} />
          <div>
            <div className="font-semibold">{def.name}</div>
            <div className="text-xs opacity-70">{def.role}</div>
          </div>
        </div>
        <button className="rounded px-1.5 text-sm opacity-70 hover:opacity-100" onClick={() => director.select(null)}>
          ✕
        </button>
      </div>
      <div className="mt-2 inline-block rounded-md bg-white/10 px-2 py-0.5 font-mono text-[11px]">{def.backend}</div>
      <p className="mt-2 text-sm leading-snug opacity-90">{def.description}</p>
      <div className="mt-3 flex items-center gap-2 text-xs">
        <span className={`h-2 w-2 rounded-full ${view.busy ? "animate-pulse bg-emerald-400" : "bg-slate-400"}`} />
        <span>{view.activity}</span>
      </div>
      {selected !== "client" && (
        <div className="mt-3 flex gap-2">
          <button className="office-btn" onClick={() => director.openDialog(selected)}>
            💬 Nói chuyện
          </button>
          <button
            className="office-btn"
            onClick={() => {
              director.setView("pov", selected);
              director.select(null);
            }}
          >
            👁️ Nhìn bằng mắt
          </button>
        </div>
      )}
      {view.log.length > 0 && (
        <div className="mt-3 max-h-36 space-y-1 overflow-y-auto border-t border-white/10 pt-2 text-xs">
          {[...view.log].reverse().map((l, i) => (
            <div key={i} className="flex gap-2">
              <span className="shrink-0 font-mono opacity-50">{fmtTime(l.t)}</span>
              <span className="opacity-90">{l.text}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Hộp hội thoại khi nói chuyện với một nhân viên */
type CaseItem = { id: string; title: string; count: number; updated?: string; pinned: boolean };

/** Nhân viên có thể mở hồ sơ cũ / vụ việc mới */
const CASE_KEEPERS = ["memory", "receptionist"];

function DialogBox({
  onAsk,
  onShowDocs,
  hasDocs,
  initialStage,
  cases,
  activeCaseId,
  live,
  busy,
  onOpenCase,
  onNewCase,
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
    <div className="office-panel pointer-events-auto w-[min(560px,calc(100vw-24px))] rounded-2xl p-4">
      <div className="flex items-center gap-3">
        <div className="h-11 w-11 shrink-0 rounded-full ring-2 ring-white/30" style={{ background: def.top }} />
        <div className="min-w-0 flex-1">
          <div className="font-semibold">
            {def.name} <span className="text-xs font-normal opacity-60">· {def.role}</span>
          </div>
          <div className="truncate text-[11px] opacity-60">Đang: {view.activity}</div>
        </div>
        <button className="rounded px-2 text-sm opacity-70 hover:opacity-100" onClick={() => director.closeDialog()}>
          ✕
        </button>
      </div>
      <div className="mt-3 rounded-xl bg-white/5 p-3 text-sm leading-relaxed ring-1 ring-white/10">
        {stage === "about" ? (
          <>
            {def.description}
            <div className="mt-2 font-mono text-[11px] opacity-60">⚙️ {def.backend}</div>
          </>
        ) : stage === "ask" ? (
          "Anh/chị cứ trình bày vấn đề, tôi sẽ chuyển cho cả văn phòng cùng xử lý."
        ) : stage === "cases" ? (
          !live ? (
            "Tủ hồ sơ chỉ lưu vụ việc thật — anh/chị bật chế độ 🟢 Live để tôi tra nhé."
          ) : cases.length === 0 ? (
            "Anh/chị chưa có hồ sơ nào ở văn phòng. Mình mở vụ việc mới nhé?"
          ) : (
            <>
              Đây là các hồ sơ của anh/chị, muốn xem tiếp vụ nào ạ?
              <div className="mt-2 max-h-48 space-y-1 overflow-y-auto pr-1">
                {cases.map((c) => (
                  <button
                    key={c.id}
                    disabled={busy}
                    className={`office-option flex w-full items-center gap-2 text-left disabled:opacity-50 ${c.id === activeCaseId ? "ring-1 ring-sky-400/60" : ""}`}
                    onClick={() => pickCase(c)}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">
                        {c.pinned ? "📌 " : "📁 "}
                        {c.title}
                      </span>
                      <span className="block text-[11px] opacity-50">
                        {c.count} tin nhắn{c.updated ? ` · ${new Date(c.updated).toLocaleString("vi-VN")}` : ""}
                        {c.id === activeCaseId ? " · đang mở" : ""}
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
        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            ask();
          }}
        >
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Ví dụ: Tôi bị công ty nợ lương 3 tháng…"
            className="min-w-0 flex-1 rounded-xl bg-white/10 px-3 py-2 text-sm outline-none ring-1 ring-white/15 focus:ring-sky-400"
          />
          <button className="rounded-xl bg-sky-600 px-4 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-40" disabled={!q.trim()}>
            Hỏi
          </button>
        </form>
      ) : stage === "cases" ? (
        <div className="mt-3 grid gap-1.5 sm:grid-cols-2">
          {live && (
            <button className="office-option" disabled={busy} onClick={newCase}>
              🆕 Mở vụ việc mới
            </button>
          )}
          <button className="office-option" onClick={() => setStage("menu")}>
            ↩️ Quay lại
          </button>
        </div>
      ) : (
        <div className="mt-3 grid gap-1.5 sm:grid-cols-2">
          <button className="office-option" onClick={() => setStage("ask")}>
            ⚖️ Tôi muốn hỏi một vấn đề pháp lý
          </button>
          {isKeeper && (
            <button className="office-option" onClick={() => setStage("cases")}>
              📂 Cho tôi xem lại hồ sơ cũ
            </button>
          )}
          {isKeeper && live && activeCaseId && (
            <button className="office-option" disabled={busy} onClick={newCase}>
              🆕 Tôi có vụ việc mới
            </button>
          )}
          <button className="office-option" onClick={() => setStage("about")}>
            🤔 Anh/chị phụ trách việc gì?
          </button>
          {canShowDocs && (
            <button className="office-option" onClick={onShowDocs}>
              📁 Cho tôi xem tài liệu vừa tìm
            </button>
          )}
          <button className="office-option" onClick={() => director.closeDialog()}>
            👋 Tạm biệt
          </button>
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
      <div className="pointer-events-none absolute left-1/2 top-1/2 z-20 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white/80 ring-2 ring-black/30" />
      {def && (
        <div className="absolute left-1/2 top-[58%] z-30 -translate-x-1/2">
          <button
            className="office-panel pointer-events-auto flex items-center gap-2 rounded-full px-4 py-2 text-sm"
            onClick={() => director.openDialog(def.id)}
          >
            {!touch && <kbd className="rounded bg-white/20 px-1.5 font-mono text-xs">E</kbd>}
            💬 Nói chuyện với <b>{def.name}</b>
            <span className="text-xs opacity-60">· {def.role}</span>
          </button>
        </div>
      )}
      {!touch && !busy && (
        <div className="pointer-events-none absolute bottom-4 left-1/2 z-20 -translate-x-1/2 rounded-full bg-black/40 px-4 py-1.5 text-[11px] text-white/80">
          Click để điều khiển chuột · WASD di chuyển · Shift chạy · E nói chuyện · Esc nhả chuột
        </div>
      )}
    </>
  );
}

/** Nói chuyện với bất kỳ nhân viên nào mà không cần đi tới (khách ngồi yên tại chỗ) */
function TalkToBar() {
  const dialog = useDialog();
  const { chars } = useOffice();
  return (
    <div className="office-panel pointer-events-auto flex max-w-[calc(100vw-24px)] items-center gap-1 overflow-x-auto rounded-2xl p-1 text-xs lg:max-w-[calc(100vw-424px)]">
      <span className="shrink-0 px-2 opacity-70">💬 Nói chuyện với:</span>
      {STAFF.map((c) => (
        <button
          key={c.id}
          className={`office-chip flex shrink-0 items-center gap-1.5 ${dialog === c.id ? "is-on" : ""}`}
          title={`${c.role} — ${chars[c.id].activity}`}
          onClick={() => {
            director.select(null);
            if (dialog === c.id) director.closeDialog();
            else director.openDialog(c.id, "menu", true);
          }}
        >
          <span className={`h-2.5 w-2.5 rounded-full ${chars[c.id].busy ? "animate-pulse" : ""}`} style={{ background: c.top }} />
          {c.name}
          <span className="hidden opacity-60 xl:inline">· {c.role}</span>
        </button>
      ))}
    </div>
  );
}

/** Chọn nhân vật để nhìn bằng mắt họ */
function PovBar() {
  const povId = usePovId();
  return (
    <div className="office-panel pointer-events-auto flex max-w-[calc(100vw-24px)] gap-1 overflow-x-auto rounded-2xl p-1 text-xs">
      {STAFF.map((c) => (
        <button
          key={c.id}
          className={`office-chip flex items-center gap-1.5 ${povId === c.id ? "is-on" : ""}`}
          onClick={() => director.setView("pov", c.id)}
        >
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: c.top }} />
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
  // Mặc định MOCK (không cần backend). Live khi ?live=1, ?chat=<id> (mở hội thoại
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
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<"normal" | "situation">("normal");
  const [tab, setTab] = useState<"chat" | "history" | "activity">("chat");
  const [docsOpen, setDocsOpen] = useState<Passage[] | null>(null);
  const [follow, setFollow] = useState(false);
  const [signs, setSigns] = useState(true);
  const [resetToken, setResetToken] = useState(0);
  const [sheetOpen, setSheetOpen] = useState(true);
  const listRef = useRef<HTMLDivElement>(null);
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

  return (
    <div className="office-root fixed inset-0 overflow-hidden">
      {/* Cảnh 3D */}
      <div
        className={`absolute left-0 right-0 top-[120px] overflow-hidden sm:top-[72px] lg:bottom-0 lg:right-[400px] lg:top-0 ${
          sheetOpen ? "bottom-[52vh]" : "bottom-[64px]"
        }`}
      >
        <OfficeScene
          onOpenDocs={() => setDocsOpen(passages)}
          follow={follow}
          showSigns={signs}
          resetToken={resetToken}
          quality={quality}
        />
        {viewMode === "fp" && touch && !dialog && <TouchControls />}
        {viewMode === "fp" && <FirstPersonHud touch={touch} busy={busy} />}
        {viewMode === "pov" && (
          <div className="pointer-events-none absolute bottom-4 left-1/2 z-20 -translate-x-1/2 rounded-full bg-black/45 px-4 py-1.5 text-xs text-white/90">
            👁️ Đang nhìn qua mắt <b>{CHARACTER_BY_ID[povId].name}</b> · {CHARACTER_BY_ID[povId].role} — kéo để liếc quanh
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
              onOpenCase={(id) => {
                openChat(id);
                setTab("chat");
                setSheetOpen(true);
              }}
              onNewCase={() => {
                reset();
                setTab("chat");
              }}
            />
          </div>
        )}
      </div>

      {/* Thanh trên */}
      <div className="pointer-events-none absolute left-0 right-0 top-0 z-30 flex flex-wrap items-start justify-between gap-2 p-3 sm:p-4 lg:right-[400px]">
        <div className="office-panel pointer-events-auto flex items-center gap-3 rounded-2xl px-3 py-2">
          <Link href="/" className="rounded-lg px-2 py-1 text-xs hover:bg-white/10" title="Về giao diện chat">
            ← Chat
          </Link>
          <div className="hidden sm:block">
            <div className="text-sm font-semibold">⚖️ Văn phòng luật 3D</div>
            <div className="text-[11px] opacity-60">
              {viewMode === "fp" ? "Bạn đang nhập vai khách hàng" : "Bấm vào nhân vật để xem họ đang làm gì"}
            </div>
          </div>
          <div className="flex gap-1 rounded-xl bg-white/5 p-0.5 text-xs">
            {([
              ["orbit", "🗺️", "Toàn cảnh"],
              ["fp", "🧍", "Nhập vai"],
              ["pov", "👁️", "Mắt nhân vật"],
            ] as const).map(([m, icon, label]) => (
              <button key={m} className={`office-chip ${viewMode === m ? "is-on" : ""}`} onClick={() => changeView(m)}>
                {icon} <span className="hidden md:inline">{label}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="office-panel pointer-events-auto flex items-center gap-1 rounded-2xl p-1 text-xs">
          {viewMode === "orbit" && (
            <>
              <button className={`office-chip ${signs ? "is-on" : ""}`} onClick={() => setSigns((v) => !v)}>
                Biển tên
              </button>
              <button className={`office-chip ${follow ? "is-on" : ""}`} onClick={() => setFollow((v) => !v)} title="Camera bám theo nhân vật đang chọn">
                Bám theo
              </button>
              <button className="office-chip" onClick={() => setResetToken((n) => n + 1)}>
                Góc nhìn gốc
              </button>
            </>
          )}
          <button
            className="office-chip"
            onClick={() => setQualityPick(quality === "high" ? "low" : "high")}
            title="Đồ hoạ cao: bóng mềm + ambient occlusion (nặng hơn)"
          >
            {quality === "high" ? "✨ Đẹp" : "⚡ Nhẹ"}
          </button>
          <span className="mx-1 h-4 w-px bg-white/15" />
          <button
            className={`office-chip ${mock ? "is-mock" : "is-on"}`}
            disabled={busy}
            onClick={() => {
              setMock((v) => !v);
              setTab("chat");
            }}
            title={mock ? "Đang dùng dữ liệu mẫu — bấm để chuyển sang backend thật" : "Đang gọi backend thật (rag_api) — bấm để về Mock"}
          >
            {mock ? "🧪 Mock" : "🟢 Live"}
          </button>
        </div>
      </div>

      {/* Thẻ nhân vật + trạng thái */}
      <div className="pointer-events-none absolute left-3 top-[124px] z-30 flex flex-col gap-2 sm:left-4 sm:top-24">
        <TalkToBar />
        {viewMode === "orbit" && !dialog && <CharacterCard />}
        {viewMode === "pov" && <PovBar />}
      </div>
      {busy && status && (
        <div className="pointer-events-none absolute left-1/2 top-[128px] z-30 -translate-x-1/2 sm:top-20 lg:bottom-6 lg:top-auto lg:left-[calc((100%-400px)/2)]">
          <div className="office-panel flex max-w-[92vw] items-center gap-2 whitespace-nowrap rounded-full px-4 py-1.5 text-xs">
            <span className="h-2 w-2 animate-pulse rounded-full bg-amber-400" />
            {status}
            {iteration && (
              <span className="rounded-full bg-white/10 px-2 py-0.5 font-mono">
                vòng {iteration.cur}/{iteration.max}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Panel chat: cột phải trên desktop, bottom sheet trên mobile */}
      <aside
        className={`office-panel absolute bottom-0 left-0 right-0 z-30 flex flex-col rounded-t-3xl transition-[height] duration-300 lg:left-auto lg:top-0 lg:h-full lg:w-[400px] lg:rounded-none lg:rounded-l-3xl ${
          sheetOpen ? "h-[52vh]" : "h-[64px]"
        }`}
      >
        <button className="mx-auto mt-2 h-1.5 w-12 shrink-0 rounded-full bg-white/30 lg:hidden" onClick={() => setSheetOpen((v) => !v)} aria-label="Thu/mở khung chat" />
        <div className="flex shrink-0 items-center gap-1 px-3 pb-2 pt-2 lg:pt-4">
          {(mock ? (["chat", "activity"] as const) : (["chat", "history", "activity"] as const)).map((t) => (
            <button
              key={t}
              className={`office-chip ${tab === t ? "is-on" : ""}`}
              onClick={() => {
                setTab(t);
                setSheetOpen(true);
              }}
            >
              {t === "chat" ? "💬 Hội thoại" : t === "history" ? `📂 Hồ sơ cũ (${chats.length})` : `📋 Hoạt động (${timeline.length})`}
            </button>
          ))}
          <div className="flex-1" />
          {(mock ? messages.length > 0 : !!activeChat) && !busy && (
            <button
              className="office-chip"
              onClick={() => {
                reset();
                setTab("chat");
              }}
            >
              Vụ việc mới
            </button>
          )}
        </div>
        {!mock && tab === "chat" && (
          <div className="shrink-0 truncate px-4 pb-1 text-[11px] opacity-60" title={activeChat?.title}>
            {activeChat ? `📁 ${activeChat.title}` : "📁 Vụ việc mới — câu hỏi đầu tiên sẽ tạo hội thoại mới"}
          </div>
        )}
        {error && <div className="mx-3 mb-1 shrink-0 rounded-lg bg-rose-500/15 px-3 py-1.5 text-xs text-rose-200">{error}</div>}

        <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto px-3 pb-2">
          {tab === "history" ? (
            chatsLoading && chats.length === 0 ? (
              <p className="pt-2 text-sm opacity-60">Đang tải hồ sơ…</p>
            ) : chats.length === 0 ? (
              <p className="pt-2 text-sm opacity-60">Chưa có hội thoại nào.</p>
            ) : (
              <div className="space-y-1 pt-1">
                {chats.map((c) => {
                  const isActive = c._id === activeChat?._id;
                  const updated = c.updated_at ?? c.updatedAt ?? c.created_at ?? c.createdAt;
                  return (
                    <button
                      key={c._id}
                      disabled={busy}
                      className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm ring-1 disabled:opacity-50 ${
                        isActive ? "bg-sky-600/25 ring-sky-400/50" : "bg-white/5 ring-white/10 hover:bg-white/10"
                      }`}
                      onClick={() => {
                        openChat(c._id);
                        setTab("chat");
                      }}
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate">
                          {c.pinned ? "📌 " : ""}
                          {c.title || "Cuộc trò chuyện mới"}
                        </span>
                        <span className="block text-[11px] opacity-50">
                          {c.messages?.length ?? 0} tin nhắn
                          {updated ? ` · ${new Date(updated).toLocaleString("vi-VN")}` : ""}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            )
          ) : tab === "chat" ? (
            messages.length === 0 ? (
              <div className="space-y-3 pt-2 text-sm">
                <p className="opacity-80">
                  Chào mừng đến văn phòng! Hãy trình bày vấn đề pháp lý của bạn — lễ tân, thư ký, luật sư và các trợ lý sẽ cùng xử lý ngay trước mắt bạn.
                </p>
                <div className="grid gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} className="rounded-xl bg-white/5 px-3 py-2 text-left text-xs ring-1 ring-white/10 hover:bg-white/10" onClick={() => submit(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-3 pt-1">
                {messages.map((m, i) => (
                  <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
                    <div
                      className={
                        m.role === "user"
                          ? "max-w-[85%] rounded-2xl rounded-br-md bg-sky-600 px-3 py-2 text-sm text-white"
                          : "office-md rounded-2xl rounded-bl-md bg-white/5 px-3 py-2 text-sm ring-1 ring-white/10"
                      }
                    >
                      {m.role === "user" ? (
                        m.content
                      ) : (
                        <>
                          <div className="mb-1 text-[11px] font-semibold opacity-60">
                            ⚖️ LS. Hải{m.cache_hit ? " · từ sổ tay" : ""}
                          </div>
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                          {!!m.passages?.length && (
                            <button className="mt-2 rounded-lg bg-amber-200/20 px-2 py-1 text-xs text-amber-200 hover:bg-amber-200/30" onClick={() => setDocsOpen(m.passages!)}>
                              📁 Xem {m.passages.length} tài liệu đã dùng
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                ))}
                {busy && (
                  <div className="flex items-center gap-2 text-xs opacity-70">
                    <span className="office-dots" /> Văn phòng đang xử lý…
                  </div>
                )}
              </div>
            )
          ) : timeline.length === 0 ? (
            <p className="pt-2 text-sm opacity-60">Chưa có hoạt động nào.</p>
          ) : (
            <div className="space-y-1.5 pt-1 text-xs">
              {timeline.map((it, i) => {
                const def = CHARACTER_BY_ID[it.who];
                return (
                  <button key={i} className="flex w-full gap-2 rounded-lg px-1 py-0.5 text-left hover:bg-white/5" onClick={() => director.select(it.who)}>
                    <span className="shrink-0 font-mono opacity-50">{fmtTime(it.t)}</span>
                    <span className="shrink-0 font-semibold" style={{ color: def.id === "lead" ? "#fbbf24" : def.top }}>
                      {def.name}
                    </span>
                    <span className="opacity-90">{it.text}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <form
          className="shrink-0 border-t border-white/10 p-3"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <div className="mb-2 flex gap-1 text-xs">
            <button type="button" className={`office-chip ${mode === "normal" ? "is-on" : ""}`} onClick={() => setMode("normal")}>
              Hỏi đáp
            </button>
            <button type="button" className={`office-chip ${mode === "situation" ? "is-on" : ""}`} onClick={() => setMode("situation")}>
              Phân tích tình huống
            </button>
          </div>
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onFocus={() => setSheetOpen(true)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  submit();
                }
              }}
              rows={1}
              placeholder={mode === "situation" ? "Mô tả tình huống của bạn…" : "Trình bày câu hỏi với văn phòng…"}
              className="max-h-32 min-h-[42px] flex-1 resize-none rounded-xl bg-white/10 px-3 py-2.5 text-sm outline-none ring-1 ring-white/15 placeholder:opacity-50 focus:ring-sky-400"
            />
            {busy ? (
              <button type="button" onClick={stop} className="h-[42px] rounded-xl bg-rose-600 px-4 text-sm font-medium text-white hover:bg-rose-500">
                Dừng
              </button>
            ) : (
              <button type="submit" disabled={!input.trim()} className="h-[42px] rounded-xl bg-sky-600 px-4 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-40">
                Gửi
              </button>
            )}
          </div>
        </form>
      </aside>

      {docsOpen && <PassagesModal passages={docsOpen} onClose={() => setDocsOpen(null)} />}
    </div>
  );
}
