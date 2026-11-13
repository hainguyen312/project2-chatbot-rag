"use client";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft, BookOpen, CircleAlert, Globe, CircleCheck, ClipboardPaste, Download, FileSearch, FileText,
  History, Link2, LoaderCircle, Moon, Plus, ScrollText, SearchX, Sun, Trash2, TriangleAlert, Upload, X,
} from "lucide-react";
import { citationOf } from "@/components/chat/MessageRow";
import { PassagePanel } from "@/components/chat/PassagePanel";
import type { Passage } from "@/store/chat-store";
import { useAuth } from "@/hooks/useAuth";
import { useThemeMode } from "@/hooks/useThemeMode";
import { getOrCreateAnonId } from "@/lib/anon-id";

// ── Kiểu dữ liệu trả về từ /review/analyze ──────────────────────────────────
/** Điều luật đã tra — cùng khuôn passages của chat (thêm ghi chú hiệu lực nếu có). */
type LawPassage = Passage & { hieu_luc?: string };
type Finding = {
  dieu_khoan: string; trich_dan: string; van_de: string;
  danh_gia: "vi_pham" | "rui_ro";
  muc_do: "cao" | "trung_binh" | "thap";
  giai_thich: string; khuyen_nghi: string;
  /** p = vị trí trong result.passages; trich = đoạn nguyên văn đã kiểm chứng ("" nếu không có) */
  can_cu: { p: number; trich: string }[];
  /** tất cả điều luật đã tra cho điều khoản này */
  tai_lieu: number[];
  /** điều luật được nhắc trong giải thích nhưng không có trong kết quả tra cứu */
  ngoai_can_cu: string[];
};
type ReviewItem = { dieu_khoan: string; trich_dan: string; van_de: string; muc_do: Finding["muc_do"]; tai_lieu: number[] };
type ReviewResult = {
  document: { type: "file" | "url" | "text"; name?: string; url?: string; chars: number; truncated: boolean };
  loai_van_ban: string; tieu_de: string;
  cac_ben: { vai_tro: string; ten: string }[];
  tom_tat: string;
  y_chinh: { muc: string; noi_dung: string; dieu_khoan: string }[];
  thieu_sot: string[];
  canh_bao: Finding[];
  can_xem_lai: ReviewItem[];
  passages: LawPassage[];
  warnings: string[];
  disclaimer: string;
  history_id?: string;
  history_error?: string;
};

/** Kết quả lưu từ phiên bản trước (căn cứ nhúng thẳng trong cảnh báo) → khuôn hiện tại. */
type LegacyCitation = { mapc: string; ma_phap_dien?: string; ten?: string; vbqppl?: string; noidung?: string; tenchude?: string; hieu_luc?: string };
type LegacyFinding = Omit<Finding, "danh_gia" | "can_cu" | "tai_lieu" | "ngoai_can_cu"> & {
  danh_gia: string; can_cu?: LegacyCitation[]; lien_quan?: LegacyCitation[];
};
function normalizeResult(raw: ReviewResult): ReviewResult {
  if (Array.isArray(raw.passages)) return { ...raw, can_xem_lai: raw.can_xem_lai ?? [] };
  const passages: LawPassage[] = [];
  const at = new Map<string, number>();
  const idx = (c: LegacyCitation) => {
    if (!at.has(c.mapc)) {
      at.set(c.mapc, passages.length);
      passages.push({
        mapc: c.mapc, ten: c.ma_phap_dien ? `Điều ${c.ma_phap_dien}. ${c.ten ?? ""}` : (c.ten ?? ""),
        vbqppl: c.vbqppl, noidung: c.noidung ?? "", tenchude: c.tenchude, source: "phapdien", hieu_luc: c.hieu_luc,
      });
    }
    return at.get(c.mapc)!;
  };
  const canh_bao: Finding[] = [];
  const can_xem_lai: ReviewItem[] = [];
  for (const f of (raw.canh_bao ?? []) as unknown as LegacyFinding[]) {
    const cited = (f.can_cu ?? []).map(idx);
    const all = [...cited, ...(f.lien_quan ?? []).map(idx)];
    if ((f.danh_gia === "vi_pham" || f.danh_gia === "rui_ro") && cited.length) {
      canh_bao.push({ ...f, danh_gia: f.danh_gia, can_cu: cited.map((p) => ({ p, trich: "" })), tai_lieu: all, ngoai_can_cu: [] });
    } else {
      can_xem_lai.push({ dieu_khoan: f.dieu_khoan, trich_dan: f.trich_dan, van_de: f.van_de, muc_do: f.muc_do, tai_lieu: all });
    }
  }
  return { ...raw, canh_bao, can_xem_lai, passages };
}

type HistoryItem = {
  id: string; created_at: string; title: string; loai_van_ban: string;
  source: { type?: "file" | "url" | "text"; name?: string; url?: string };
  counts: { vi_pham?: number; canh_bao?: number; thieu_sot?: number };
  file: { name: string; content_type: string; size: number } | null;
};
type HistoryDetail = HistoryItem & { text: string; result: ReviewResult };
type Mode = "file" | "url" | "text";

const STAGES = [
  { id: "extract", label: "Đọc văn bản" },
  { id: "analyze", label: "Tóm tắt, tìm điều khoản rủi ro" },
  { id: "search", label: "Tra cứu Pháp Điển và web" },
  { id: "verify", label: "Đối chiếu quy định" },
];
const ACCEPT = ".pdf,.docx,.txt,.md";
const MAX_BYTES = 15 * 1024 * 1024;

const VERDICT: Record<Finding["danh_gia"], { label: string; tone: Tone }> = {
  vi_pham: { label: "Có dấu hiệu trái luật", tone: "warn" },
  rui_ro: { label: "Rủi ro / bất lợi", tone: "caution" },
};
const SEVERITY: Record<Finding["muc_do"], string> = { cao: "Mức cao", trung_binh: "Mức trung bình", thap: "Mức thấp" };

type Tone = "warn" | "caution" | "muted" | "ok";
const TONE: Record<Tone, { bg: string; fg: string; bd: string }> = {
  warn: { bg: "var(--danger-bg)", fg: "var(--danger-text)", bd: "var(--danger-border)" },
  caution: { bg: "var(--status-bg)", fg: "var(--danger-text)", bd: "var(--status-border)" },
  muted: { bg: "var(--status-bg)", fg: "var(--status-text)", bd: "var(--status-border)" },
  ok: { bg: "var(--ok-bg)", fg: "var(--ok-text)", bd: "transparent" },
};

function Pill({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  const t = TONE[tone];
  return (
    <span className="inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[11px] font-medium"
      style={{ background: t.bg, color: t.fg, border: `1px solid ${t.bd}` }}>{children}</span>
  );
}

function Section({ title, children, aside }: { title: string; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section className="mt-8">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** Đọc luồng SSE từ fetch: gọi onEvent cho từng `data: {...}`. */
async function readSSE(res: Response, onEvent: (ev: Record<string, unknown>) => void) {
  const reader = res.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try { onEvent(JSON.parse(line.slice(5).trim())); } catch { /* bỏ qua dòng hỏng */ }
    }
  }
}

/** Mở panel "Căn cứ pháp lý" với một nhóm điều luật, cuộn tới điều thứ focus (nếu có). */
type OpenPassages = (indices: number[], focus?: number) => void;

const isWeb = (p: LawPassage) => p.source === "web" || p.source === "web_realtime";
function hostOf(url?: string) {
  try { return url ? new URL(url).hostname.replace(/^www\./, "") : ""; } catch { return ""; }
}
function lawLabel(p: LawPassage) {
  return isWeb(p) ? hostOf(p.url) || "Nguồn web" : citationOf(p).label;
}
const TRUST: Record<string, string> = { high: "Nguồn tin cậy", medium: "Tham khảo" };

/** Chip căn cứ — cùng kiểu với chip "Căn cứ pháp lý" dưới câu trả lời của chat (Pháp Điển: đỏ son; web: viền xám). */
function CitationChip({ n, p, onClick }: { n: number; p: LawPassage; onClick: () => void }) {
  if (isWeb(p)) {
    return (
      <button type="button" onClick={onClick} title={p.ten}
        className="inline-flex max-w-full items-center gap-1.5 rounded-md px-2.5 py-1.5 text-left text-[13px] transition-colors hover:bg-[var(--bg-hover)]"
        style={{ border: "1px solid var(--border-strong)", color: "var(--text-secondary)" }}>
        <Globe size={14} className="shrink-0" aria-hidden />
        <span className="shrink-0 tabular-nums">[{n}]</span>
        <span className="truncate">{lawLabel(p)}</span>
        {p.trust_level && <span className="shrink-0 text-[11px]" style={{ color: "var(--text-muted)" }}>· {TRUST[p.trust_level] ?? p.trust_level}</span>}
      </button>
    );
  }
  return (
    <button type="button" onClick={onClick} title={citationOf(p).title}
      className="inline-flex max-w-full items-center gap-1.5 rounded-md px-2.5 py-1.5 text-left text-[13px] font-medium transition-colors hover:brightness-95"
      style={{ background: "var(--accent-light)", border: "1px solid var(--accent)", color: "var(--accent-text)" }}>
      <BookOpen size={14} className="shrink-0" aria-hidden />
      <span className="shrink-0 tabular-nums">[{n}]</span>
      <span className="truncate">{lawLabel(p)}</span>
    </button>
  );
}

/** Văn bản có dấu [n] → [n] thành nút mở đúng điều luật căn cứ. */
function WithMarkers({ text, onMarker, max }: { text: string; onMarker: (n: number) => void; max: number }) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <>
      {parts.map((part, i) => {
        const m = part.match(/^\[(\d+)\]$/);
        const n = m ? Number(m[1]) : 0;
        if (!m || n < 1 || n > max) return <span key={i}>{part}</span>;
        return (
          <button key={i} type="button" onClick={() => onMarker(n)} aria-label={`Xem căn cứ ${n}`}
            className="mx-0.5 inline-flex items-center rounded px-1 align-baseline text-[11px] font-semibold leading-4 hover:brightness-95"
            style={{ background: "var(--accent-light)", color: "var(--accent-text)" }}>
            {n}
          </button>
        );
      })}
    </>
  );
}

function FindingCard({ f, passages, onOpen }: { f: Finding; passages: LawPassage[]; onOpen: OpenPassages }) {
  const v = VERDICT[f.danh_gia];
  const Icon = f.danh_gia === "vi_pham" ? TriangleAlert : CircleAlert;
  const openCite = (n: number) => onOpen(f.tai_lieu, f.can_cu[n - 1]?.p);
  const quoted = f.can_cu.map((c, i) => ({ ...c, n: i + 1 })).filter((c) => c.trich);
  return (
    <article className="rounded-xl p-4" style={{ background: "var(--bg-surface)", border: `1px solid ${f.danh_gia === "vi_pham" ? "var(--danger-border)" : "var(--border)"}` }}>
      <header className="flex flex-wrap items-center gap-2">
        <Icon size={17} style={{ color: TONE[v.tone].fg }} aria-hidden />
        <h3 className="text-sm font-semibold">{f.dieu_khoan || "Điều khoản"}</h3>
        <Pill tone={v.tone}>{v.label}</Pill>
        <Pill tone="muted">{SEVERITY[f.muc_do]}</Pill>
      </header>

      {/* Đối chiếu: hợp đồng ↔ quy định (nguyên văn điều luật đã tra) */}
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {f.trich_dan && (
          <div className="rounded-lg px-3 py-2" style={{ background: "var(--bg-base)" }}>
            <p className="text-[11px] font-medium uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>Hợp đồng</p>
            <p className="mt-1 text-[13px] italic leading-relaxed" style={{ color: "var(--text-secondary)" }}>“{f.trich_dan}”</p>
          </div>
        )}
        {quoted.map((c) => (
          <button key={c.n} type="button" onClick={() => openCite(c.n)}
            className="rounded-lg px-3 py-2 text-left transition-colors hover:brightness-[0.98]"
            style={isWeb(passages[c.p])
              ? { background: "var(--bg-base)", border: "1px solid var(--border)" }
              : { background: "var(--accent-light)" }}>
            <p className="text-[11px] font-medium uppercase tracking-wide"
              style={{ color: isWeb(passages[c.p]) ? "var(--text-muted)" : "var(--accent-text)" }}>
              {isWeb(passages[c.p]) ? "Nguồn web" : "Quy định"} [{c.n}] · {lawLabel(passages[c.p])}
            </p>
            <p className="mt-1 text-[13px] italic leading-relaxed" style={{ color: "var(--text-primary)" }}>“{c.trich}”</p>
          </button>
        ))}
      </div>

      <p className="mt-3 text-sm leading-relaxed">
        <WithMarkers text={f.giai_thich || f.van_de} onMarker={openCite} max={f.can_cu.length} />
      </p>
      {f.khuyen_nghi && (
        <p className="mt-2 text-sm leading-relaxed">
          <span className="font-medium">Khuyến nghị: </span>
          <span style={{ color: "var(--text-secondary)" }}><WithMarkers text={f.khuyen_nghi} onMarker={openCite} max={f.can_cu.length} /></span>
        </p>
      )}
      {f.ngoai_can_cu.length > 0 && (
        <p className="mt-2 text-xs leading-relaxed" style={{ color: "var(--danger-text)" }}>
          Giải thích có nhắc {f.ngoai_can_cu.join("; ")} — điều này không có trong các nguồn đã tra nên chưa được kiểm chứng.
        </p>
      )}

      <div className="mt-4">
        <div className="mb-2 text-xs font-medium" style={{ color: "var(--text-secondary)" }}>Căn cứ pháp lý</div>
        <div className="flex flex-wrap gap-2">
          {f.can_cu.map((c, i) => <CitationChip key={c.p} n={i + 1} p={passages[c.p]} onClick={() => openCite(i + 1)} />)}
        </div>
        {passages.some((_, i) => f.can_cu.some((c) => c.p === i) && passages[i].hieu_luc) && (
          <p className="mt-2 text-xs" style={{ color: "var(--danger-text)" }}>
            {f.can_cu.map((c) => passages[c.p].hieu_luc).filter(Boolean).join(" · ")}
          </p>
        )}
      </div>
      {f.tai_lieu.length > 0 && (
        <button type="button" onClick={() => onOpen(f.tai_lieu)}
          className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs transition-colors hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-muted)" }}>
          <BookOpen size={14} aria-hidden />Xem {f.tai_lieu.length} tài liệu đã tra
        </button>
      )}
    </article>
  );
}

function ReviewCard({ item, onOpen }: { item: ReviewItem; onOpen: OpenPassages }) {
  return (
    <article className="rounded-xl px-4 py-3" style={{ border: "1px dashed var(--border-strong)" }}>
      <header className="flex flex-wrap items-center gap-2">
        <SearchX size={16} style={{ color: "var(--text-muted)" }} aria-hidden />
        <h3 className="text-sm font-semibold">{item.dieu_khoan || "Điều khoản"}</h3>
        <Pill tone="muted">Chưa tìm thấy căn cứ</Pill>
      </header>
      {item.trich_dan && (
        <p className="mt-2 text-[13px] italic" style={{ color: "var(--text-secondary)" }}>“{item.trich_dan}”</p>
      )}
      <p className="mt-2 text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>{item.van_de}</p>
      {item.tai_lieu.length > 0 && (
        <button type="button" onClick={() => onOpen(item.tai_lieu)}
          className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs transition-colors hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-muted)" }}>
          <BookOpen size={14} aria-hidden />Xem {item.tai_lieu.length} tài liệu đã tra
        </button>
      )}
    </article>
  );
}

function Result({ r, onOpen }: { r: ReviewResult; onOpen: OpenPassages }) {
  const violations = r.canh_bao.filter((f) => f.danh_gia === "vi_pham").length;
  const src = r.document.type === "file" ? r.document.name : r.document.type === "url" ? r.document.url : "Nội dung dán vào";
  const allIdx = r.passages.map((_, i) => i);
  return (
    <div className="pb-16">
      <div className="mt-8 rounded-xl p-5" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
        <div className="flex flex-wrap items-center gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
          {r.loai_van_ban && <Pill tone="muted">{r.loai_van_ban}</Pill>}
          <span className="min-w-0 truncate">{src}</span>
        </div>
        <h2 className="mt-2 text-lg font-semibold leading-snug">{r.tieu_de || "Văn bản"}</h2>
        {r.cac_ben.length > 0 && (
          <dl className="mt-3 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_1fr]">
            {r.cac_ben.map((b, i) => (
              <div key={i} className="contents">
                <dt style={{ color: "var(--text-muted)" }}>{b.vai_tro}</dt>
                <dd>{b.ten}</dd>
              </div>
            ))}
          </dl>
        )}
        <p className="mt-4 text-sm leading-relaxed">{r.tom_tat}</p>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {r.canh_bao.length === 0
            ? <Pill tone="ok">Không có cảnh báo có căn cứ pháp lý</Pill>
            : <>
                {violations > 0 && <Pill tone="warn">{violations} điều khoản có dấu hiệu trái luật</Pill>}
                <Pill tone="muted">{r.canh_bao.length} cảnh báo</Pill>
              </>}
          {r.can_xem_lai.length > 0 && <Pill tone="muted">{r.can_xem_lai.length} điều khoản nên xem lại</Pill>}
          {r.passages.length > 0 && (
            <button type="button" onClick={() => onOpen(allIdx)}
              className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs transition-colors hover:bg-[var(--bg-hover)]"
              style={{ color: "var(--text-muted)" }}>
              <BookOpen size={14} aria-hidden />Xem {r.passages.length} tài liệu đã tra
            </button>
          )}
        </div>
      </div>

      {r.warnings.length > 0 && (
        <div className="mt-4 flex flex-col gap-1 rounded-lg px-3 py-2 text-[13px]" style={{ background: "var(--status-bg)", color: "var(--status-text)" }}>
          {r.warnings.map((w, i) => <p key={i}>{w}</p>)}
        </div>
      )}

      <Section title="Cảnh báo pháp lý" aside={r.canh_bao.length > 0 && <span className="text-xs" style={{ color: "var(--text-muted)" }}>Mỗi cảnh báo kèm căn cứ từ Pháp Điển hoặc nguồn web</span>}>
        {r.canh_bao.length === 0 ? (
          <p className="flex items-center gap-2 text-sm" style={{ color: "var(--ok-text)" }}>
            <CircleCheck size={16} aria-hidden /> Không phát hiện điều khoản trái luật có căn cứ ở phần đã rà soát.
          </p>
        ) : (
          <div className="flex flex-col gap-3">{r.canh_bao.map((f, i) => <FindingCard key={i} f={f} passages={r.passages} onOpen={onOpen} />)}</div>
        )}
      </Section>

      {r.can_xem_lai.length > 0 && (
        <Section title="Điều khoản nên xem lại">
          <p className="-mt-1 mb-3 text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
            Có dấu hiệu bất thường nhưng chưa tìm được căn cứ (Pháp Điển hoặc nguồn web tin cậy) đủ để kết luận — không
            phải cảnh báo vi phạm. Nên hỏi luật sư hoặc tra cứu thêm.
          </p>
          <div className="flex flex-col gap-2">{r.can_xem_lai.map((it, i) => <ReviewCard key={i} item={it} onOpen={onOpen} />)}</div>
        </Section>
      )}

      {r.y_chinh.length > 0 && (
        <Section title="Các ý chính">
          <dl className="divide-y rounded-xl" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderColor: "var(--border)" }}>
            {r.y_chinh.map((k, i) => (
              <div key={i} className="grid gap-1 px-4 py-3 sm:grid-cols-[10rem_1fr] sm:gap-4" style={{ borderColor: "var(--border)" }}>
                <dt className="text-sm font-medium">
                  {k.muc}
                  {k.dieu_khoan && <span className="block text-xs font-normal" style={{ color: "var(--text-muted)" }}>{k.dieu_khoan}</span>}
                </dt>
                <dd className="text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>{k.noi_dung}</dd>
              </div>
            ))}
          </dl>
        </Section>
      )}

      {r.thieu_sot.length > 0 && (
        <Section title="Nội dung nên bổ sung">
          <ul className="list-disc space-y-1 pl-5 text-sm" style={{ color: "var(--text-secondary)" }}>
            {r.thieu_sot.map((t, i) => <li key={i}>{t}</li>)}
          </ul>
        </Section>
      )}

      <p className="mt-10 border-t pt-4 text-xs leading-relaxed" style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>
        {r.disclaimer}
      </p>
    </div>
  );
}

// ── Lịch sử ─────────────────────────────────────────────────────────────────
function formatDate(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function sourceLabel(src: HistoryItem["source"]) {
  if (src.type === "file") return src.name ?? "File";
  if (src.type === "url") return src.url ?? "Link";
  return "Nội dung dán vào";
}

/** Gọi /api/review/history kèm token Firebase (nếu đăng nhập) và user_id (id ẩn danh). */
function useHistoryApi(userId: string) {
  const { getIdToken } = useAuth();
  return useCallback(async (path: string, init: RequestInit = {}) => {
    const token = await getIdToken();
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const sep = path.includes("?") ? "&" : "?";
    const res = await fetch(`/api/review/history${path}${sep}user_id=${encodeURIComponent(userId)}`,
      { ...init, headers, cache: "no-store" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(typeof data?.detail === "string" ? data.detail : `Lỗi ${res.status}`);
    }
    return res;
  }, [getIdToken, userId]);
}

function HistoryList({ items, activeId, loading, error, onOpen, onNew }: {
  items: HistoryItem[]; activeId: string | null; loading: boolean; error: string | null;
  onOpen: (id: string) => void; onNew: () => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 px-3 pb-2 pt-4">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold"><History size={15} aria-hidden />Lịch sử rà soát</h2>
        <button type="button" onClick={onNew}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-secondary)" }}>
          <Plus size={14} aria-hidden />Phân tích mới
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {loading && <p className="px-2 py-3 text-xs" style={{ color: "var(--text-muted)" }}>Đang tải…</p>}
        {error && <p className="px-2 py-3 text-xs" style={{ color: "var(--danger-text)" }}>{error}</p>}
        {!loading && !error && items.length === 0 && (
          <p className="px-2 py-3 text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
            Chưa có lượt rà soát nào. Kết quả phân tích sẽ được lưu ở đây.
          </p>
        )}
        <ul className="flex flex-col gap-0.5">
          {items.map((it) => {
            const active = it.id === activeId;
            return (
              <li key={it.id}>
                <button type="button" onClick={() => onOpen(it.id)} aria-current={active ? "true" : undefined}
                  className="w-full rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-[var(--bg-hover)]"
                  style={active ? { background: "var(--bg-msgbtn)" } : undefined}>
                  <span className="line-clamp-2 text-[13px] font-medium leading-snug">{it.title || sourceLabel(it.source)}</span>
                  <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
                    <span>{formatDate(it.created_at)}</span>
                    {(it.counts.vi_pham ?? 0) > 0
                      ? <Pill tone="warn">{it.counts.vi_pham} trái luật</Pill>
                      : (it.counts.canh_bao ?? 0) > 0
                        ? <Pill tone="muted">{it.counts.canh_bao} cảnh báo</Pill>
                        : <Pill tone="ok">Không cảnh báo</Pill>}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function SavedBar({ item, onDownload, onDelete, busy }: {
  item: HistoryDetail; onDownload: () => void; onDelete: () => void; busy: boolean;
}) {
  const [showText, setShowText] = useState(false);
  return (
    <div className="mt-8">
      <div className="flex flex-wrap items-center gap-2">
        <p className="mr-auto text-xs" style={{ color: "var(--text-muted)" }}>Rà soát lúc {formatDate(item.created_at)}</p>
        {item.file && (
          <button type="button" onClick={onDownload} disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium hover:bg-[var(--bg-hover)] disabled:opacity-50"
            style={{ border: "1px solid var(--border-strong)" }}>
            <Download size={14} aria-hidden />Tải file gốc
          </button>
        )}
        {item.text && (
          <button type="button" onClick={() => setShowText((v) => !v)} aria-expanded={showText}
            className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium hover:bg-[var(--bg-hover)]"
            style={{ border: "1px solid var(--border-strong)" }}>
            <ScrollText size={14} aria-hidden />{showText ? "Ẩn văn bản" : "Xem văn bản"}
          </button>
        )}
        <button type="button" onClick={onDelete} disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium disabled:opacity-50"
          style={{ border: "1px solid var(--danger-border)", color: "var(--danger-text)" }}>
          <Trash2 size={14} aria-hidden />Xoá
        </button>
      </div>
      {showText && (
        <pre className="mt-3 max-h-[28rem] overflow-y-auto whitespace-pre-wrap rounded-xl p-4 font-sans text-[13px] leading-relaxed"
          style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
          {item.text}
        </pre>
      )}
    </div>
  );
}

function setUrlId(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("id", id); else url.searchParams.delete("id");
  window.history.replaceState(null, "", url);
}

export default function ReviewPage() {
  const [dark, toggleDark] = useThemeMode();
  const { user, loading: authLoading, getIdToken } = useAuth();
  const [anonId, setAnonId] = useState("");
  useEffect(() => { setAnonId(getOrCreateAnonId()); }, []);
  const userId = authLoading ? "" : (user?.uid || anonId);
  const historyApi = useHistoryApi(userId);

  const [mode, setMode] = useState<Mode>("file");
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [save, setSave] = useState(true);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ReviewResult | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const [items, setItems] = useState<HistoryItem[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [saved, setSaved] = useState<HistoryDetail | null>(null);
  const [openingId, setOpeningId] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [drawer, setDrawer] = useState(false);
  /** Panel "Căn cứ pháp lý": nhóm điều luật đang xem + điều cần cuộn tới (nonce để bấm lại vẫn cuộn) */
  const [panel, setPanel] = useState<{ idx: number[]; focus?: number; nonce: number } | null>(null);
  const openPassages = useCallback((idx: number[], focus?: number) => {
    setPanel({ idx, focus, nonce: Date.now() });
  }, []);

  const refreshList = useCallback(async () => {
    if (!userId) return;
    setListLoading(true); setListError(null);
    try {
      const data = await (await historyApi("")).json();
      setItems(data.items ?? []);
    } catch (e) {
      setListError(e instanceof Error ? e.message : "Không tải được lịch sử");
    } finally {
      setListLoading(false);
    }
  }, [historyApi, userId]);

  const openSaved = useCallback(async (id: string) => {
    setDrawer(false);
    setOpeningId(id); setError(null);
    try {
      const data: HistoryDetail = await (await historyApi(`/${encodeURIComponent(id)}`)).json();
      setSaved(data); setResult(normalizeResult(data.result)); setPanel(null); setUrlId(id);
      window.scrollTo({ top: 0 });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không mở được lượt rà soát");
      setUrlId(null);
    } finally {
      setOpeningId(null);
    }
  }, [historyApi]);

  // Tải danh sách khi biết người dùng; mở ?id=… nếu có
  useEffect(() => {
    if (!userId) return;
    void refreshList();
    const id = new URLSearchParams(window.location.search).get("id");
    if (id) void openSaved(id);
  }, [userId, refreshList, openSaved]);

  const startNew = () => {
    setSaved(null); setResult(null); setError(null); setDrawer(false); setPanel(null); setUrlId(null);
  };

  const pickFile = (f: File | undefined | null) => {
    if (!f) return;
    if (f.size > MAX_BYTES) { setError("File lớn hơn 15MB"); return; }
    setError(null);
    setFile(f);
  };

  const ready = mode === "file" ? !!file : mode === "url" ? /^https?:\/\/\S+$/i.test(url.trim()) : text.trim().length >= 200;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready || busy) return;
    const fd = new FormData();
    if (mode === "file" && file) fd.append("file", file);
    if (mode === "url") fd.append("url", url.trim());
    if (mode === "text") fd.append("text", text);
    fd.append("save", save ? "true" : "false");
    if (userId) fd.append("user_id", userId);
    setBusy(true); setError(null); setResult(null); setSaved(null); setPanel(null); setUrlId(null);
    setStage(mode === "text" ? "analyze" : "extract");
    try {
      const token = await getIdToken();
      const res = await fetch("/api/review", {
        method: "POST", body: fd, headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      let got: ReviewResult | null = null;
      await readSSE(res, (ev) => {
        if (ev.type === "status") setStage(String(ev.stage));
        else if (ev.type === "result") { got = normalizeResult(ev as unknown as ReviewResult); setResult(got); }
        else if (ev.type === "error") setError(String(ev.message));
      });
      const done = got as ReviewResult | null;
      if (!done) setError((prev) => prev ?? "Không nhận được kết quả, vui lòng thử lại");
      else if (done.history_id) {
        setFile(null); setUrl(""); setText("");
        await refreshList();
        await openSaved(done.history_id);
      }
    } catch {
      setError("Mất kết nối khi phân tích, vui lòng thử lại");
    } finally {
      setBusy(false); setStage(null);
    }
  }

  async function downloadFile() {
    if (!saved?.file) return;
    setActionBusy(true);
    try {
      const blob = await (await historyApi(`/${encodeURIComponent(saved.id)}/file`)).blob();
      const href = URL.createObjectURL(blob);
      const a = Object.assign(document.createElement("a"), { href, download: saved.file.name || "van_ban" });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không tải được file");
    } finally {
      setActionBusy(false);
    }
  }

  async function deleteSaved() {
    if (!saved || !window.confirm("Xoá lượt rà soát này cùng file gốc? Không thể hoàn tác.")) return;
    setActionBusy(true);
    try {
      await historyApi(`/${encodeURIComponent(saved.id)}`, { method: "DELETE" });
      setItems((xs) => xs.filter((x) => x.id !== saved.id));
      startNew();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không xoá được");
    } finally {
      setActionBusy(false);
    }
  }

  const stageIdx = STAGES.findIndex((s) => s.id === stage);
  const tabs: { id: Mode; label: string; icon: typeof Upload }[] = [
    { id: "file", label: "Tải file", icon: Upload },
    { id: "url", label: "Dán link", icon: Link2 },
    { id: "text", label: "Dán nội dung", icon: ClipboardPaste },
  ];
  const activeId = saved?.id ?? null;
  const panelPassages = panel && result ? panel.idx.map((i) => result.passages[i]).filter(Boolean) : null;
  const panelFocus = panel?.focus !== undefined ? panel.idx.indexOf(panel.focus) : undefined;
  const historyList = (
    <HistoryList items={items} activeId={activeId} loading={listLoading && items.length === 0}
      error={listError} onOpen={openSaved} onNew={startNew} />
  );

  return (
    <div className="flex h-screen flex-col" style={{ background: "var(--bg-base)", color: "var(--text-primary)" }}>
      <header className="flex shrink-0 items-center gap-3 px-4 py-3 md:px-6" style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-surface)" }}>
        <Link href="/" className="inline-flex h-9 w-9 items-center justify-center rounded-lg hover:bg-[var(--bg-hover)]" aria-label="Về trang chat">
          <ArrowLeft size={18} />
        </Link>
        <FileSearch size={20} style={{ color: "var(--accent)" }} aria-hidden />
        <h1 className="text-base font-semibold">Rà soát văn bản</h1>
        <div className="ml-auto flex items-center gap-1">
          <button type="button" onClick={() => setDrawer(true)}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg px-2.5 text-sm hover:bg-[var(--bg-hover)] lg:hidden"
            style={{ color: "var(--text-secondary)" }}>
            <History size={16} aria-hidden />Lịch sử
          </button>
          <button type="button" onClick={toggleDark} aria-label={dark ? "Chuyển nền sáng" : "Chuyển nền tối"}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg hover:bg-[var(--bg-hover)]" style={{ color: "var(--text-secondary)" }}>
            {dark ? <Sun size={17} /> : <Moon size={17} />}
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-72 shrink-0 lg:block" style={{ borderRight: "1px solid var(--border)", background: "var(--bg-sidebar)" }}>
          {historyList}
        </aside>

        {drawer && (
          <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Lịch sử rà soát">
            <div className="absolute inset-0 bg-black/40" onClick={() => setDrawer(false)} />
            <div className="absolute inset-y-0 left-0 flex w-[85%] max-w-xs flex-col" style={{ background: "var(--bg-sidebar)" }}>
              <button type="button" onClick={() => setDrawer(false)} aria-label="Đóng"
                className="absolute right-2 top-3 inline-flex h-8 w-8 items-center justify-center rounded-md hover:bg-[var(--bg-hover)]">
                <X size={16} />
              </button>
              {historyList}
            </div>
          </div>
        )}

        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full px-4" style={{ maxWidth: "var(--reading-width)" }}>
            {openingId && (
              <p className="mt-8 flex items-center gap-2 text-sm" style={{ color: "var(--text-muted)" }}>
                <LoaderCircle size={16} className="animate-spin" aria-hidden />Đang mở…
              </p>
            )}

            {!saved && !openingId && (
              <>
                <div className="pt-8">
                  <h2 className="text-xl font-semibold">Kiểm tra hợp đồng trước khi ký</h2>
                  <p className="mt-1.5 text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                    Tải lên hợp đồng mua bán, thuê nhà, lao động, vay, đặt cọc… Hệ thống tóm tắt các ý chính và cảnh báo
                    điều khoản có nguy cơ trái luật, kèm căn cứ từ Pháp Điển và nguồn web pháp luật tin cậy.
                  </p>
                </div>

                <form onSubmit={submit} className="mt-6 rounded-xl p-4" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                  <div role="tablist" className="flex gap-1 rounded-lg p-1" style={{ background: "var(--bg-base)" }}>
                    {tabs.map((t) => (
                      <button key={t.id} type="button" role="tab" aria-selected={mode === t.id} onClick={() => setMode(t.id)} disabled={busy}
                        className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-sm transition-colors"
                        style={mode === t.id
                          ? { background: "var(--bg-surface)", color: "var(--text-primary)", boxShadow: "var(--shadow-sm)", fontWeight: 500 }
                          : { color: "var(--text-secondary)" }}>
                        <t.icon size={15} aria-hidden /><span>{t.label}</span>
                      </button>
                    ))}
                  </div>

                  <div className="mt-4">
                    {mode === "file" && (
                      file ? (
                        <div className="flex items-center gap-3 rounded-lg px-3 py-3" style={{ border: "1px solid var(--border-strong)" }}>
                          <FileText size={20} style={{ color: "var(--text-muted)" }} aria-hidden />
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium">{file.name}</p>
                            <p className="text-xs" style={{ color: "var(--text-muted)" }}>{(file.size / 1024).toFixed(0)} KB</p>
                          </div>
                          <button type="button" onClick={() => setFile(null)} disabled={busy} aria-label="Bỏ file"
                            className="inline-flex h-8 w-8 items-center justify-center rounded-md hover:bg-[var(--bg-hover)]">
                            <X size={16} />
                          </button>
                        </div>
                      ) : (
                        <button type="button" onClick={() => inputRef.current?.click()}
                          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
                          onDragLeave={() => setDragging(false)}
                          onDrop={(e) => { e.preventDefault(); setDragging(false); pickFile(e.dataTransfer.files?.[0]); }}
                          className="flex w-full flex-col items-center gap-2 rounded-lg px-4 py-8 text-center transition-colors"
                          style={{ border: `1.5px dashed ${dragging ? "var(--accent)" : "var(--border-strong)"}`, background: dragging ? "var(--accent-light)" : "transparent" }}>
                          <Upload size={22} style={{ color: "var(--text-muted)" }} aria-hidden />
                          <span className="text-sm font-medium">Kéo thả file vào đây hoặc bấm để chọn</span>
                          <span className="text-xs" style={{ color: "var(--text-muted)" }}>PDF (có lớp chữ), DOCX hoặc TXT · tối đa 15MB</span>
                        </button>
                      )
                    )}
                    <input ref={inputRef} type="file" accept={ACCEPT} hidden onChange={(e) => { pickFile(e.target.files?.[0]); e.target.value = ""; }} />

                    {mode === "url" && (
                      <div>
                        <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} disabled={busy}
                          placeholder="https://… (link PDF, trang web hoặc Google Docs công khai)"
                          className="w-full rounded-lg px-3 py-2.5 text-sm outline-none focus:shadow-[var(--focus-ring)]"
                          style={{ background: "var(--bg-input)", border: "1px solid var(--border-strong)", color: "var(--text-primary)" }} />
                        <p className="mt-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
                          Trang cần đăng nhập sẽ không đọc được — hãy tải file về rồi chọn &quot;Tải file&quot;.
                        </p>
                      </div>
                    )}

                    {mode === "text" && (
                      <div>
                        <textarea value={text} onChange={(e) => setText(e.target.value)} disabled={busy} rows={10}
                          placeholder="Dán toàn bộ nội dung hợp đồng vào đây…"
                          className="w-full resize-y rounded-lg px-3 py-2.5 text-sm leading-relaxed outline-none focus:shadow-[var(--focus-ring)]"
                          style={{ background: "var(--bg-input)", border: "1px solid var(--border-strong)", color: "var(--text-primary)" }} />
                        <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                          {text.trim().length < 200 ? `Cần ít nhất 200 ký tự (hiện ${text.trim().length})` : `${text.trim().length.toLocaleString("vi-VN")} ký tự`}
                        </p>
                      </div>
                    )}
                  </div>

                  <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                    <label className="flex cursor-pointer items-center gap-2 text-xs" style={{ color: "var(--text-secondary)" }}>
                      <input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} disabled={busy}
                        className="h-4 w-4 accent-[var(--accent)]" />
                      Lưu file và kết quả vào lịch sử
                    </label>
                    <button type="submit" disabled={!ready || busy}
                      className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                      style={{ background: "var(--accent)", color: "var(--text-on-accent)" }}>
                      {busy ? <LoaderCircle size={16} className="animate-spin" aria-hidden /> : <FileSearch size={16} aria-hidden />}
                      {busy ? "Đang phân tích…" : "Phân tích văn bản"}
                    </button>
                  </div>
                  <p className="mt-2 text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
                    {save
                      ? (user ? "Lịch sử gắn với tài khoản của bạn" : "Chưa đăng nhập: lịch sử chỉ gắn với trình duyệt này, đăng nhập để giữ lâu dài")
                        + " — bạn có thể xoá bất cứ lúc nào."
                      : "Không lưu: file và kết quả chỉ hiển thị lần này."}
                  </p>
                </form>
              </>
            )}

            {busy && (
              <ol className="mt-6 flex flex-col gap-2" aria-live="polite">
                {STAGES.map((s, i) => {
                  const done = stageIdx > i, active = stageIdx === i;
                  return (
                    <li key={s.id} className="flex items-center gap-2.5 text-sm"
                      style={{ color: done ? "var(--text-secondary)" : active ? "var(--text-primary)" : "var(--text-muted)" }}>
                      {done ? <CircleCheck size={16} style={{ color: "var(--ok-text)" }} aria-hidden />
                        : active ? <LoaderCircle size={16} className="animate-spin" aria-hidden />
                        : <span className="inline-block h-4 w-4 rounded-full" style={{ border: "1.5px solid var(--border-strong)" }} aria-hidden />}
                      <span className={active ? "font-medium" : undefined}>{s.label}</span>
                    </li>
                  );
                })}
                <li className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>Thường mất 20–60 giây tuỳ độ dài văn bản.</li>
              </ol>
            )}

            {error && (
              <div role="alert" className="mt-6 flex items-start gap-2 rounded-lg px-3 py-2.5 text-sm"
                style={{ background: "var(--danger-bg)", color: "var(--danger-text)", border: "1px solid var(--danger-border)" }}>
                <CircleAlert size={16} className="mt-0.5 shrink-0" aria-hidden /><span>{error}</span>
              </div>
            )}

            {result?.history_error && !saved && (
              <p className="mt-4 text-xs" style={{ color: "var(--danger-text)" }}>{result.history_error} — kết quả bên dưới chưa được lưu.</p>
            )}

            {saved && !openingId && <SavedBar item={saved} onDownload={downloadFile} onDelete={deleteSaved} busy={actionBusy} />}
            {result && !openingId && <Result r={result} onOpen={openPassages} />}
            {!result && <div className="pb-16" />}
          </div>
        </main>

        {panelPassages && (
          <div className="hidden md:flex">
            <PassagePanel key={panel!.nonce} passages={panelPassages} focusIndex={panelFocus} onClose={() => setPanel(null)} />
          </div>
        )}
      </div>

      {panelPassages && (
        <div className="fixed inset-0 z-40 flex flex-col md:hidden" role="dialog" aria-modal="true" aria-label="Căn cứ pháp lý"
          style={{ background: "var(--bg-sidebar)" }}>
          <div className="flex shrink-0 items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
            <span className="flex items-center gap-2 text-sm font-semibold"><BookOpen size={16} style={{ color: "var(--accent)" }} aria-hidden />Căn cứ pháp lý</span>
            <button type="button" onClick={() => setPanel(null)} aria-label="Đóng"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md hover:bg-[var(--bg-hover)]"><X size={16} /></button>
          </div>
          <div className="min-h-0 flex-1">
            <PassagePanel key={panel!.nonce} passages={panelPassages} focusIndex={panelFocus} onClose={() => setPanel(null)} mobileEmbedded />
          </div>
        </div>
      )}
    </div>
  );
}
