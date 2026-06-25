"use client";
import { Message, Passage } from "@/store/chat-store";
import Image from "next/image";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

const BOT_AVATAR_SRC = toSvgDataUri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="32" fill="#0f172a"/>
<path d="M22 18h20a10 10 0 0 1 10 10v10a12 12 0 0 1-12 12H24A12 12 0 0 1 12 38V28a10 10 0 0 1 10-10z" fill="#e2e8f0"/>
<circle cx="26" cy="34" r="4" fill="#0f172a"/>
<circle cx="38" cy="34" r="4" fill="#0f172a"/>
<path d="M28 46c2.5 2 5.5 2 8 0" stroke="#0f172a" stroke-width="3" fill="none" stroke-linecap="round"/>
<path d="M32 12v8" stroke="#e2e8f0" stroke-width="4" stroke-linecap="round"/>
</svg>`);

const USER_AVATAR_SRC = toSvgDataUri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#534AB7"/><stop offset="1" stop-color="#1d4ed8"/>
</linearGradient></defs>
<rect width="64" height="64" rx="32" fill="url(#g)"/>
<circle cx="32" cy="26" r="12" fill="#fff" opacity="0.95"/>
<path d="M14 54c3.5-11 13.2-16 18-16s14.5 5 18 16" fill="#fff" opacity="0.95"/>
</svg>`);

function toSvgDataUri(svg: string) {
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

export function Avatar({ type }: { type: "bot" | "user" }) {
  return (
    <Image
      src={type === "bot" ? BOT_AVATAR_SRC : USER_AVATAR_SRC}
      alt={type}
      width={28}
      height={28}
      // md:28 desktop, 24 mobile
      className="h-6 w-6 shrink-0 rounded-full object-cover md:h-7 md:w-7"
    />
  );
}

export function BotBubble({
  content,
  isSelected = false,
}: {
  content: string;
  isSelected?: boolean;
}) {
  return (
    <div
      className="w-fit rounded-2xl rounded-bl-md px-3 py-2.5 text-sm leading-6 shadow-sm transition-all md:px-4 md:py-3 md:leading-7"
      style={{
        // Mobile: 90vw max, Desktop: 82% capped at 760px
        maxWidth: "min(90vw, 760px)",
        background: "var(--bg-msg-bot)",
        border: isSelected ? "1px solid #534AB7" : "0.5px solid var(--border)",
        color: "var(--text-primary)",
      }}
    >
      <div className="[&_a]:underline [&_code]:rounded [&_code]:px-1 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:p-3 [&_ul]:list-disc [&_ul]:pl-4 [&_ol]:list-decimal [&_ol]:pl-4 [&_li]:my-0.5 [&_p]:my-1.5 md:[&_ul]:pl-5 md:[&_ol]:pl-5 md:[&_li]:my-1 md:[&_p]:my-2">
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{content}</ReactMarkdown>
      </div>
    </div>
  );
}

export function MessageRow({
  m,
  idx,
  isSelected,
  onSelect,
  onSpeak,
  isSpeaking,
  isLoadingTTS,
  chatId,
  onSaveTtsUrl,
  isAnyTTSActive,
  onSaveFeedback,
}: {
  m: Message;
  idx: number;
  isSelected: boolean;
  onSelect: (passages: Passage[]) => void;
  onSpeak: (
    text: string,
    idx: number,
    chatId: string,
    cachedUrl?: string,
    onUrlReceived?: (url: string) => void
  ) => void;
  isSpeaking: boolean;
  isLoadingTTS: boolean;
  chatId: string;
  onSaveTtsUrl: (msgIdx: number, url: string) => void;
  isAnyTTSActive: boolean;
  onSaveFeedback?: (msgIdx: number, rating: "up" | "down") => void;
}) {
  const isUser      = m.role === "user";
  const hasPassages = !isUser && (m.passages?.length ?? 0) > 0;

  const confidencePct = m.confidence_score != null ? Math.round(m.confidence_score * 100) : null;
  const confidenceColor =
    confidencePct == null ? null :
    confidencePct >= 70 ? "#16a34a" :
    confidencePct >= 40 ? "#d97706" :
    "#dc2626";
  const confidenceBg =
    confidencePct == null ? null :
    confidencePct >= 70 ? "rgba(22,163,74,0.08)" :
    confidencePct >= 40 ? "rgba(217,119,6,0.08)" :
    "rgba(220,38,38,0.08)";

  return (
    <div className={`flex items-end gap-1.5 md:gap-2 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && <Avatar type="bot" />}

      {isUser ? (
        // ── User bubble ──────────────────────────────────────────────────────
        <div
          className="w-fit rounded-2xl rounded-br-md px-3 py-2.5 text-sm leading-6 md:px-4 md:py-3 md:leading-7"
          style={{
            maxWidth: "min(82vw, 640px)",
            background: "#534AB7",
            color: "#ffffff",
          }}
        >
          <div className="whitespace-pre-wrap break-words">{m.content}</div>
        </div>
      ) : (
        // ── Bot bubble ───────────────────────────────────────────────────────
        <div className="relative min-w-0">
          <BotBubble content={m.content} isSelected={isSelected} />

          {/* Hallucination warning */}
          {m.hallucination_warning && (
            <div className="mt-1.5 flex items-start gap-1.5 rounded-lg px-2.5 py-2 text-xs"
              style={{ background: "rgba(220,38,38,0.07)", border: "0.5px solid rgba(220,38,38,0.25)", color: "#b91c1c" }}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth="2" strokeLinecap="round" className="mt-0.5 shrink-0">
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
              </svg>
              <span>Câu trả lời này có thể chưa có đủ cơ sở pháp lý. Hãy kiểm tra lại từ nguồn chính thức.</span>
            </div>
          )}

          {/* Action buttons */}
          <div className="mt-1 flex flex-wrap items-center gap-1">

            {/* TTS button */}
            <button
              type="button"
              onClick={() =>
                onSpeak(
                  m.content,
                  idx,
                  chatId,
                  m.tts_url ?? undefined,
                  (url) => onSaveTtsUrl(idx, url)
                )
              }
              disabled={isLoadingTTS}
              title={
                isLoadingTTS
                  ? "Đang tải giọng đọc..."
                  : isSpeaking
                  ? "Dừng đọc"
                  : m.tts_url
                  ? "Phát giọng đọc (đã lưu)"
                  : "Đọc to (AI)"
              }
              className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs transition disabled:opacity-60"
              style={{
                background:
                  isSpeaking || isLoadingTTS
                    ? "#EEEDFE"
                    : m.tts_url
                    ? "rgba(83,74,183,0.08)"
                    : "var(--bg-surface)",
                border:
                  isSpeaking || isLoadingTTS
                    ? "0.5px solid #534AB7"
                    : m.tts_url
                    ? "0.5px solid rgba(83,74,183,0.4)"
                    : "0.5px solid var(--border)",
                color:
                  isSpeaking || isLoadingTTS
                    ? "#3C3489"
                    : m.tts_url
                    ? "#534AB7"
                    : "var(--text-muted)",
              }}
            >
              {isLoadingTTS ? (
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" strokeWidth="2" className="animate-spin">
                  <circle cx="12" cy="12" r="10" strokeOpacity="0.25"/>
                  <path d="M12 2a10 10 0 0 1 10 10" strokeOpacity="1"/>
                </svg>
              ) : isSpeaking ? (
                <span className="flex items-end gap-[2px] h-3">
                  {[1, 2, 3].map((i) => (
                    <span key={i} className="w-[3px] rounded-sm"
                      style={{
                        background: "#534AB7",
                        height: i === 2 ? "100%" : "60%",
                        animation: `eq-bar ${0.4 + i * 0.15}s ease-in-out infinite alternate`,
                      }}
                    />
                  ))}
                </span>
              ) : m.tts_url ? (
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                  <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
                  <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
                </svg>
              ) : (
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                  <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
                  <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
                </svg>
              )}
              <span>
                {isLoadingTTS ? "Đang tải..." : isSpeaking ? "Dừng" : m.tts_url ? "Phát" : "Đọc"}
              </span>
            </button>

            {/* Passages button */}
            {hasPassages && (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onSelect(m.passages!); }}
                className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs transition"
                style={{
                  background: isSelected ? "#EEEDFE" : "var(--bg-surface)",
                  border: isSelected ? "0.5px solid #534AB7" : "0.5px solid var(--border)",
                  color: isSelected ? "#3C3489" : "var(--text-muted)",
                }}
              >
                <span>📄</span>
                <span>{m.passages!.length} tài liệu</span>
              </button>
            )}

            {/* Confidence score badge */}
            {confidencePct != null && (
              <span className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs"
                style={{ background: confidenceBg!, border: `0.5px solid ${confidenceColor}30`, color: confidenceColor! }}>
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                </svg>
                <span>{confidencePct}%</span>
              </span>
            )}

            {/* Cache hit badge */}
            {m.cache_hit && (
              <span className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs"
                style={{ background: "rgba(83,74,183,0.07)", border: "0.5px solid rgba(83,74,183,0.25)", color: "#534AB7" }}>
                <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M13 10V3L4 14h7v7l9-11h-7z"/>
                </svg>
                <span>Từ cache</span>
              </span>
            )}

            {/* Memory used badge */}
            {!!m.memories_used?.length && (
              <span
                title={m.memories_used.map((mm) => `(${mm.type}) ${mm.fact}`).join("\n")}
                className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs"
                style={{ background: "rgba(99,102,241,0.08)", border: "0.5px solid rgba(99,102,241,0.3)", color: "#4f46e5" }}>
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                  strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z"/>
                  <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z"/>
                </svg>
                <span>đã dùng {m.memories_used.length} ký ức</span>
              </span>
            )}

            {/* Thumbs up / down feedback */}
            {onSaveFeedback && (
              <>
                <button
                  type="button"
                  onClick={() => onSaveFeedback(idx, "up")}
                  title="Câu trả lời hữu ích"
                  className="flex items-center justify-center rounded-lg px-2 py-1 text-xs transition"
                  style={{
                    background: m.feedback === "up" ? "rgba(22,163,74,0.10)" : "var(--bg-surface)",
                    border: m.feedback === "up" ? "0.5px solid #16a34a" : "0.5px solid var(--border)",
                    color: m.feedback === "up" ? "#16a34a" : "var(--text-muted)",
                  }}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill={m.feedback === "up" ? "currentColor" : "none"}
                    stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z"/>
                    <path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/>
                  </svg>
                </button>
                <button
                  type="button"
                  onClick={() => onSaveFeedback(idx, "down")}
                  title="Câu trả lời chưa hữu ích"
                  className="flex items-center justify-center rounded-lg px-2 py-1 text-xs transition"
                  style={{
                    background: m.feedback === "down" ? "rgba(220,38,38,0.10)" : "var(--bg-surface)",
                    border: m.feedback === "down" ? "0.5px solid #dc2626" : "0.5px solid var(--border)",
                    color: m.feedback === "down" ? "#dc2626" : "var(--text-muted)",
                  }}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill={m.feedback === "down" ? "currentColor" : "none"}
                    stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10z"/>
                    <path d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/>
                  </svg>
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {isUser && <Avatar type="user" />}
    </div>
  );
}