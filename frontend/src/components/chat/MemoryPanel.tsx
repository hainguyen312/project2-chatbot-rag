"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Brain, Trash2, X, RefreshCw } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";

type MemoryItem = {
  _id?: string;
  milvus_id?: number;
  fact: string;
  mem_type: string;
  timestamp?: string;
  version?: number;
  source_chat_id?: string | null;
};

const TYPE_LABEL: Record<string, string> = {
  core: "Hồ sơ",
  episodic: "Tình huống",
  semantic: "Chủ đề",
  procedural: "Cách trả lời",
};

const TYPE_COLOR: Record<string, string> = {
  core: "bg-blue-100 text-blue-800",
  episodic: "bg-amber-100 text-amber-800",
  semantic: "bg-emerald-100 text-emerald-800",
  procedural: "bg-purple-100 text-purple-800",
};

export function MemoryPanelButton({ userId }: { userId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-xs text-gray-700 hover:bg-gray-50"
        title="Bộ nhớ của tôi"
      >
        <Brain size={14} /> Bộ nhớ của tôi
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

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  const removeOne = async (memId: number | undefined) => {
    if (memId == null) return;
    if (!confirm("Xoá ký ức này?")) return;
    await fetch(`/api/memory/${encodeURIComponent(userId)}/${memId}`, {
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
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b px-5 py-3">
          <div className="flex items-center gap-2">
            <Brain size={18} className="text-indigo-600" />
            <h2 className="text-base font-semibold text-gray-900">Bộ nhớ của tôi</h2>
            <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
              {items.length}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={load}
              title="Tải lại"
              className="rounded p-1.5 text-gray-500 hover:bg-gray-100"
            >
              <RefreshCw size={16} />
            </button>
            <button
              onClick={removeAll}
              disabled={!items.length}
              className="inline-flex items-center gap-1 rounded-md border border-red-300 bg-white px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
            >
              <Trash2 size={14} /> Xoá hết
            </button>
            <button onClick={onClose} className="rounded p-1.5 text-gray-500 hover:bg-gray-100">
              <X size={18} />
            </button>
          </div>
        </div>

        <div className="overflow-y-auto px-5 py-4">
          {loading && <p className="py-8 text-center text-sm text-gray-500">Đang tải…</p>}
          {err && <p className="py-8 text-center text-sm text-red-600">{err}</p>}
          {!loading && !err && items.length === 0 && (
            <p className="py-12 text-center text-sm text-gray-500">
              Chưa có ký ức nào. Cứ trò chuyện, hệ thống sẽ tự ghi nhớ thông tin hữu ích.
            </p>
          )}
          <ul className="space-y-2">
            {items.map((m, idx) => (
              <li
                key={m._id ?? `${m.milvus_id}-${idx}`}
                className="flex items-start gap-3 rounded-lg border border-gray-200 bg-white p-3"
              >
                <span
                  className={`inline-flex shrink-0 rounded px-2 py-0.5 text-[10px] font-medium ${
                    TYPE_COLOR[m.mem_type] ?? "bg-gray-100 text-gray-800"
                  }`}
                >
                  {TYPE_LABEL[m.mem_type] ?? m.mem_type}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-gray-900">{m.fact}</p>
                  <p className="mt-1 text-[11px] text-gray-500">
                    {m.timestamp ? new Date(m.timestamp).toLocaleString("vi-VN") : ""}
                    {m.version && m.version > 1 ? ` · v${m.version}` : ""}
                  </p>
                </div>
                <button
                  onClick={() => removeOne(m.milvus_id)}
                  className="rounded p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600"
                  title="Xoá"
                >
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="border-t px-5 py-2 text-[11px] text-gray-500">
          User ID: <code className="rounded bg-gray-100 px-1.5 py-0.5">{userId}</code>
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
      className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700"
    >
      <Brain size={10} /> đã dùng {count} ký ức
    </span>
  );
}
