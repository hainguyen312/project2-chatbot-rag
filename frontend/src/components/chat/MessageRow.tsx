"use client";
import { Message, Passage } from "@/store/chat-store";
import { AlertTriangle, BookOpen, Brain, Globe, ThumbsDown, ThumbsUp, Volume2, Zap } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

// ── Căn cứ pháp lý ───────────────────────────────────────────────────────────
const LOAI_VB =
  "Hiến pháp|Bộ luật|Luật|Pháp lệnh|Nghị quyết liên tịch|Nghị quyết|Nghị định|Thông tư liên tịch|Thông tư|Quyết định";
const VB_RE = new RegExp(`Điều\\s+(\\d+[a-zđ]?)\\s+(${LOAI_VB})(?:[^,()]*?số\\s+([^\\s,()]+))?`, "i");

type Citation = { label: string; title: string; dieu?: string; so?: string; passage: Passage };

/** "(Điều 8 Nghị định số 100/2019/NĐ-CP, có hiệu lực …)" → Điều 8, Nghị định 100/2019/NĐ-CP */
export function citationOf(p: Passage): Citation {
  const title = (p.ten || "").replace(/^\s*Điều\s+[\w.\-]+\.\s*/u, "").trim() || p.ten;
  const m = (p.vbqppl || "").match(VB_RE);
  if (m) {
    const [, dieu, loai, so] = m;
    return { label: `Điều ${dieu}, ${loai}${so ? ` ${so}` : ""}`, title, dieu, so, passage: p };
  }
  return { label: title, title, passage: p };
}

const norm = (s: string) => s.normalize("NFC").toLowerCase().replace(/\s+/g, " ");

/** Chỉ giữ các điều luật mà câu trả lời THỰC SỰ trích (khớp số điều + số hiệu văn bản). */
export function citedPassages(answer: string, passages: Passage[]): Citation[] {
  const a = norm(answer);
  const seen = new Set<string>();
  const out: Citation[] = [];
  for (const p of passages) {
    if (p.source === "web") continue;
    const c = citationOf(p);
    if (!c.dieu || !c.so) continue;
    const so = norm(c.so);
    const soShort = so.split("/").slice(0, 2).join("/"); // "100/2019/nđ-cp" → "100/2019"
    const hasDoc = a.includes(so) || a.includes(soShort);
    const hasDieu = new RegExp(`điều ${c.dieu.toLowerCase()}(?![0-9])`).test(a);
    if (hasDoc && hasDieu && !seen.has(c.label)) {
      seen.add(c.label);
      out.push(c);
    }
  }
  return out;
}

function hostOf(url?: string) {
  try { return url ? new URL(url).hostname.replace(/^www\./, "") : ""; } catch { return ""; }
}

// ── Thành phần dùng chung ────────────────────────────────────────────────────
export function BotBubble({ content }: { content: string; isSelected?: boolean }) {
  return (
    <div className="answer-prose min-w-0 break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{content}</ReactMarkdown>
    </div>
  );
}

function QuietButton({
  children, onClick, title, active, tone = "neutral", disabled,
}: {
  children: React.ReactNode; onClick?: () => void; title: string; active?: boolean;
  tone?: "neutral" | "ok" | "warn"; disabled?: boolean;
}) {
  const activeColor = tone === "ok" ? "var(--ok-text)" : tone === "warn" ? "var(--danger-text)" : "var(--text-primary)";
  const activeBg = tone === "ok" ? "var(--ok-bg)" : tone === "warn" ? "var(--danger-bg)" : "var(--bg-hover)";
  return (
    <button
      type="button" onClick={onClick} title={title} aria-label={title} aria-pressed={active} disabled={disabled}
      className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-60"
      style={{ color: active ? activeColor : "var(--text-muted)", background: active ? activeBg : undefined }}
    >
      {children}
    </button>
  );
}

// ── Một lượt hội thoại ───────────────────────────────────────────────────────
export function MessageRow({
  m, idx, isSelected, onSelect, onSpeak, isSpeaking, isLoadingTTS, chatId, onSaveTtsUrl, onSaveFeedback,
}: {
  m: Message;
  idx: number;
  isSelected: boolean;
  onSelect: (passages: Passage[]) => void;
  onSpeak: (text: string, idx: number, chatId: string, cachedUrl?: string, onUrlReceived?: (url: string) => void) => void;
  isSpeaking: boolean;
  isLoadingTTS: boolean;
  chatId: string;
  onSaveTtsUrl: (msgIdx: number, url: string) => void;
  isAnyTTSActive: boolean;
  onSaveFeedback?: (msgIdx: number, rating: "up" | "down") => void;
}) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div
          className="w-fit max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md px-4 py-2.5 text-[15px] leading-relaxed md:max-w-[75%]"
          style={{ background: "var(--bg-msg-user)", color: "var(--text-primary)" }}
        >
          {m.content}
        </div>
      </div>
    );
  }

  const passages = m.passages ?? [];
  const cited = citedPassages(m.content, passages);
  const webSources = passages.filter((p) => p.source === "web" && p.url);
  const confidencePct = m.confidence_score != null ? Math.round(m.confidence_score * 100) : null;
  const open = () => onSelect(passages);

  return (
    <article className="min-w-0" aria-label="Câu trả lời">
      <BotBubble content={m.content} />

      {m.hallucination_warning && (
        <p className="mt-3 flex items-start gap-2 rounded-lg px-3 py-2 text-sm"
          style={{ background: "var(--danger-bg)", border: "1px solid var(--danger-border)", color: "var(--danger-text)" }}>
          <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
          Câu trả lời này có thể chưa đủ cơ sở pháp lý. Hãy đối chiếu văn bản chính thức.
        </p>
      )}

      {/* Căn cứ pháp lý: điểm nhấn duy nhất của giao diện */}
      {(cited.length > 0 || webSources.length > 0) && (
        <div className="mt-4">
          <div className="mb-2 text-xs font-medium" style={{ color: "var(--text-secondary)" }}>Căn cứ pháp lý</div>
          <div className="flex flex-wrap gap-2">
            {cited.slice(0, 5).map((c) => (
              <button
                key={c.label} type="button" onClick={open} title={c.title}
                className="inline-flex max-w-full items-center gap-1.5 rounded-md px-2.5 py-1.5 text-left text-[13px] font-medium transition-colors hover:brightness-95"
                style={{ background: "var(--accent-light)", border: "1px solid var(--accent)", color: "var(--accent-text)" }}
              >
                <BookOpen size={14} className="shrink-0" aria-hidden />
                <span className="truncate">{c.label}</span>
              </button>
            ))}
            {webSources.slice(0, 3).map((p) => (
              <a
                key={p.url} href={p.url} target="_blank" rel="noreferrer" title={p.ten}
                className="inline-flex max-w-full items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors hover:bg-[var(--bg-hover)]"
                style={{ border: "1px solid var(--border-strong)", color: "var(--text-secondary)" }}
              >
                <Globe size={14} className="shrink-0" aria-hidden />
                <span className="truncate">{hostOf(p.url)}</span>
              </a>
            ))}
          </div>
        </div>
      )}

      {/* Hành động + thông tin phụ: im lặng, không tranh chỗ với căn cứ */}
      <div className="mt-3 flex flex-wrap items-center gap-1 text-xs" style={{ color: "var(--text-muted)" }}>
        {passages.length > 0 && (
          <QuietButton onClick={open} title="Xem các tài liệu đã tra cứu" active={isSelected}>
            <BookOpen size={14} aria-hidden />Xem {passages.length} tài liệu đã tra
          </QuietButton>
        )}
        <QuietButton
          onClick={() => onSpeak(m.content, idx, chatId, m.tts_url ?? undefined, (url) => onSaveTtsUrl(idx, url))}
          title={isLoadingTTS ? "Đang tải giọng đọc" : isSpeaking ? "Dừng đọc" : "Đọc to câu trả lời"}
          active={isSpeaking || isLoadingTTS} disabled={isLoadingTTS}
        >
          {isSpeaking ? (
            <span className="flex h-3 items-end gap-[2px]" aria-hidden>
              {[1, 2, 3].map((i) => (
                <span key={i} className="w-[3px] rounded-sm"
                  style={{ background: "currentColor", height: i === 2 ? "100%" : "60%",
                    animation: `eq-bar ${0.4 + i * 0.15}s ease-in-out infinite alternate` }} />
              ))}
            </span>
          ) : <Volume2 size={14} aria-hidden />}
          {isLoadingTTS ? "Đang tải" : isSpeaking ? "Dừng" : "Đọc"}
        </QuietButton>
        {onSaveFeedback && (
          <>
            <QuietButton onClick={() => onSaveFeedback(idx, "up")} title="Câu trả lời hữu ích"
              active={m.feedback === "up"} tone="ok">
              <ThumbsUp size={14} aria-hidden />
            </QuietButton>
            <QuietButton onClick={() => onSaveFeedback(idx, "down")} title="Câu trả lời chưa hữu ích"
              active={m.feedback === "down"} tone="warn">
              <ThumbsDown size={14} aria-hidden />
            </QuietButton>
          </>
        )}
        <span className="ml-auto flex flex-wrap items-center gap-3 pl-2">
          {confidencePct != null && (
            <span className="inline-flex items-center gap-1.5" title="Độ tin cậy ước tính từ các tài liệu tra được">
              <span className="h-1.5 w-1.5 rounded-full" aria-hidden
                style={{ background: confidencePct >= 70 ? "var(--ok-text)" : confidencePct >= 40 ? "var(--text-muted)" : "var(--danger-text)" }} />
              Độ tin cậy {confidencePct}%
            </span>
          )}
          {!!m.memories_used?.length && (
            <span className="inline-flex items-center gap-1"
              title={m.memories_used.map((mm) => `(${mm.type}) ${mm.fact}`).join("\n")}>
              <Brain size={13} aria-hidden />Dùng {m.memories_used.length} thông tin về bạn
            </span>
          )}
          {m.cache_hit && (
            <span className="inline-flex items-center gap-1" title="Trả lời lại từ một câu hỏi tương tự trước đó">
              <Zap size={13} aria-hidden />Trả lời đã lưu
            </span>
          )}
        </span>
      </div>
    </article>
  );
}
