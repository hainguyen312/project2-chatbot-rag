"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Brain, Trash2, X, RefreshCw } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";

type MemoryItem = {
  _id?: string;
  mem_id?: string;
  /** Bản ghi cũ (trước khi gộp về một kho) */
  milvus_id?: number;
  fact: string;
  mem_type: string;
  timestamp?: string;
  version?: number;
  source_chat_id?: string | null;
  /** Tình huống đã quá lâu → không còn dùng khi trả lời (vẫn giữ để bạn xem/xoá) */
  expired?: boolean;
  /** Dữ liệu cá nhân nhạy cảm (tố tụng hình sự, sức khoẻ, tôn giáo…) */
  sensitive?: boolean;
};

type MemoryMode = "no_sensitive" | "full" | "off";
const MODE_OPTIONS: { value: MemoryMode; label: string; hint: string }[] = [
  { value: "no_sensitive", label: "Ghi nhớ, trừ thông tin nhạy cảm",
    hint: "Không lưu chuyện hình sự, sức khoẻ, tôn giáo, đời sống riêng tư." },
  { value: "full", label: "Ghi nhớ cả thông tin nhạy cảm",
    hint: "Câu trả lời sát hoàn cảnh hơn. Bạn có thể xoá bất cứ lúc nào." },
  { value: "off", label: "Tắt bộ nhớ",
    hint: "Không ghi thêm và không dùng thông tin đã lưu khi trả lời." },
];

const TYPE_LABEL: Record<string, string> = {
  core: "Hồ sơ",
  episodic: "Tình huống",
  semantic: "Chủ đề",
  procedural: "Cách trả lời",
};

const TYPE_COLOR: Record<string, string> = {
  core: "bg-[var(--bg-msg-user)] text-[var(--text-primary)]",
  episodic: "bg-[var(--danger-bg)] text-[var(--danger-text)]",
  semantic: "bg-[var(--ok-bg)] text-[var(--ok-text)]",
  procedural: "bg-purple-100 text-purple-800",
};

export function MemoryPanelButton({ userId }: { userId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex h-9 items-center gap-1.5 rounded-md px-2.5 text-sm text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
        title="Những điều trợ lý ghi nhớ về bạn"
      >
        <Brain size={16} aria-hidden /> <span className="hidden md:inline">Bộ nhớ</span>
      </button>
      {open && <MemoryModal userId={userId} onClose={() => setOpen(false)} />}
    </>
  );
}

function MemoryModal({ userId, onClose }: { userId: string; onClose: () => void }) {
  const { getIdToken } = useAuth();
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [mode, setMode] = useState<MemoryMode | null>(null);
  const [savingMode, setSavingMode] = useState(false);

  const authHeaders = async (): Promise<HeadersInit> => {
    const t = await getIdToken();
    return t ? { Authorization: `Bearer ${t}` } : {};
  };

  const load = async () => {
    setLoading(true);
    setErr(null);
    try {
      const res = await fetch(`/api/memory/${encodeURIComponent(userId)}`, {
        cache: "no-store",
        headers: await authHeaders(),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail ?? `HTTP ${res.status}`);
      setItems(data?.memories ?? []);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  };

  const loadMode = async () => {
    try {
      const res = await fetch(`/api/memory/${encodeURIComponent(userId)}/settings`, {
        cache: "no-store", headers: await authHeaders(),
      });
      const data = await res.json();
      if (res.ok && data?.mode) setMode(data.mode);
    } catch {}
  };

  const changeMode = async (next: MemoryMode) => {
    const prev = mode;
    setMode(next);
    setSavingMode(true);
    try {
      const res = await fetch(`/api/memory/${encodeURIComponent(userId)}/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...(await authHeaders()) },
        body: JSON.stringify({ mode: next }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({})))?.detail ?? `HTTP ${res.status}`);
    } catch (e) {
      setMode(prev);
      setErr(`Không lưu được cài đặt: ${String(e)}`);
    } finally {
      setSavingMode(false);
    }
  };

  useEffect(() => {
    load();
    loadMode();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  const removeOne = async (memId: string | undefined) => {
    if (!memId) return;
    if (!confirm("Xoá ký ức này?")) return;
    await fetch(`/api/memory/${encodeURIComponent(userId)}/${encodeURIComponent(memId)}`, {
      method: "DELETE",
      headers: await authHeaders(),
    });
    await load();
  };

  const removeAll = async () => {
    if (!confirm("Xoá TOÀN BỘ bộ nhớ của bạn? Hành động này không thể hoàn tác.")) return;
    await fetch(`/api/memory/${encodeURIComponent(userId)}`, {
      method: "DELETE",
      headers: await authHeaders(),
    });
    await load();
  };

  if (typeof document === "undefined") return null;
  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl bg-[var(--bg-surface)] shadow-2xl">
        <div className="flex items-center justify-between border-b px-5 py-3">
          <div className="flex items-center gap-2">
            <Brain size={18} className="text-[var(--accent-text)]" />
            <h2 className="text-base font-semibold text-[var(--text-primary)]">Bộ nhớ của tôi</h2>
            <span className="rounded bg-[var(--bg-hover)] px-2 py-0.5 text-xs text-[var(--text-secondary)]">
              {items.length}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={load}
              title="Tải lại"
              className="rounded p-1.5 text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
            >
              <RefreshCw size={16} />
            </button>
            <button
              onClick={removeAll}
              disabled={!items.length}
              className="inline-flex items-center gap-1 rounded-md border border-[var(--danger-border)] bg-[var(--bg-surface)] px-2 py-1 text-xs text-[var(--danger-text)] hover:bg-[var(--danger-bg)] disabled:opacity-50"
            >
              <Trash2 size={14} /> Xoá hết
            </button>
            <button onClick={onClose} className="rounded p-1.5 text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]">
              <X size={18} />
            </button>
          </div>
        </div>

        <fieldset className="border-b border-[var(--border)] px-5 py-4" disabled={mode == null || savingMode}>
          <legend className="sr-only">Chế độ bộ nhớ</legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {MODE_OPTIONS.map((o) => (
              <label key={o.value}
                className="flex cursor-pointer flex-col gap-1 rounded-lg border p-3 text-sm transition-colors"
                style={mode === o.value
                  ? { borderColor: "var(--text-primary)", background: "var(--bg-hover)" }
                  : { borderColor: "var(--border)" }}>
                <span className="flex items-center gap-2 font-medium text-[var(--text-primary)]">
                  <input type="radio" name="memory-mode" value={o.value} checked={mode === o.value}
                    onChange={() => changeMode(o.value)} className="accent-[var(--text-primary)]" />
                  {o.label}
                </span>
                <span className="text-xs leading-relaxed text-[var(--text-secondary)]">{o.hint}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="overflow-y-auto px-5 py-4">
          {loading && <p className="py-8 text-center text-sm text-[var(--text-secondary)]">Đang tải…</p>}
          {err && <p className="py-8 text-center text-sm text-[var(--danger-text)]">{err}</p>}
          {!loading && !err && items.length === 0 && (
            <p className="py-12 text-center text-sm text-[var(--text-secondary)]">
              Chưa có ký ức nào. Cứ trò chuyện, hệ thống sẽ tự ghi nhớ thông tin hữu ích.
            </p>
          )}
          <ul className="space-y-2">
            {items.map((m, idx) => (
              <li
                key={m.mem_id ?? m._id ?? `${m.milvus_id}-${idx}`}
                className="flex items-start gap-3 rounded-lg border border-[var(--border-strong)] bg-[var(--bg-surface)] p-3"
                style={m.expired ? { opacity: 0.65 } : undefined}
              >
                <span
                  className={`inline-flex shrink-0 rounded px-2 py-0.5 text-[10px] font-medium ${
                    TYPE_COLOR[m.mem_type] ?? "bg-[var(--bg-hover)] text-[var(--text-primary)]"
                  }`}
                >
                  {TYPE_LABEL[m.mem_type] ?? m.mem_type}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-[var(--text-primary)]">{m.fact}</p>
                  <p className="mt-1 text-[11px] text-[var(--text-secondary)]">
                    {m.timestamp ? new Date(m.timestamp).toLocaleString("vi-VN") : ""}
                    {m.version && m.version > 1 ? ` · v${m.version}` : ""}
                  </p>
                  {m.sensitive && (
                    <p className="mt-1 text-[11px] text-[var(--danger-text)]">
                      Thông tin nhạy cảm{mode === "no_sensitive" ? " — đang không được dùng khi trả lời" : ""}.
                    </p>
                  )}
                  {m.expired && (
                    <p className="mt-1 text-[11px] text-[var(--text-muted)]">
                      Đã cũ nên không còn dùng khi trả lời. Xoá nếu không cần nữa.
                    </p>
                  )}
                </div>
                <button
                  onClick={() => removeOne(m.mem_id)}
                  disabled={!m.mem_id}
                  className="rounded p-1.5 text-[var(--text-muted)] hover:bg-[var(--danger-bg)] hover:text-[var(--danger-text)]"
                  title="Xoá"
                >
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="border-t px-5 py-2 text-[11px] text-[var(--text-secondary)]">
          User ID: <code className="rounded bg-[var(--bg-hover)] px-1.5 py-0.5">{userId}</code>
        </div>
      </div>
    </div>,
    document.body
  );
}

export function MemoryBadge({ count }: { count: number }) {
  if (!count) return null;
  return (
    <span
      title={`Đã dùng ${count} ký ức cá nhân để cá nhân hóa câu trả lời`}
      className="inline-flex items-center gap-1 rounded-full bg-[var(--accent-light)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--accent-text)]"
    >
      <Brain size={10} /> đã dùng {count} ký ức
    </span>
  );
}
