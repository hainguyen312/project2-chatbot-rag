"use client";
import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ChevronRight, FileText, Library, Link2, RefreshCw } from "lucide-react";
import { BUSY, StagingSummary, useAdminFetch } from "./api";
import { StagingDetailView } from "./StagingDetail";
import { Badge, Button, Notice, StatusBadge, fmtTime } from "./ui";

const FILTERS = [
  { id: "active", label: "Đang xử lý / chờ duyệt" },
  { id: "published", label: "Đã duyệt" },
  { id: "rejected", label: "Đã từ chối" },
  { id: "", label: "Tất cả" },
];

export function ReviewTab() {
  const api = useAdminFetch();
  const [filter, setFilter] = useState("active");
  const [items, setItems] = useState<StagingSummary[] | null>(null);
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    api<{ items: StagingSummary[] }>(`staging?status=${encodeURIComponent(filter)}`)
      .then((r) => { if (!cancelled) { setItems(r.items); setError(""); } })
      .catch((e) => { if (!cancelled) setError((e as Error).message); });
    return () => { cancelled = true; };
  }, [api, filter, tick]);

  // Tự làm mới khi còn mục đang xử lý / đang ghi
  const anyBusy = !!items?.some((i) => BUSY.has(i.status));
  useEffect(() => {
    if (!anyBusy) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [anyBusy, reload]);

  if (openId) {
    return <StagingDetailView id={openId} onBack={() => { setOpenId(null); reload(); }} />;
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button key={f.id} type="button" onClick={() => setFilter(f.id)}
            className="rounded-full px-3 py-1 text-sm transition-colors hover:bg-[var(--bg-hover)]"
            style={{ background: filter === f.id ? "var(--bg-hover)" : undefined, fontWeight: filter === f.id ? 600 : 400,
              color: filter === f.id ? "var(--text-primary)" : "var(--text-secondary)" }}>
            {f.label}
          </button>
        ))}
        <span className="ml-auto" />
        <Button onClick={reload}><RefreshCw size={14} aria-hidden />Làm mới</Button>
      </div>

      {error && <Notice tone="warn">{error}</Notice>}
      {items === null && !error && <p className="text-sm" style={{ color: "var(--text-muted)" }}>Đang tải…</p>}
      {items?.length === 0 && <p className="text-sm" style={{ color: "var(--text-muted)" }}>Không có mục nào.</p>}

      <ul className="space-y-2">
        {items?.map((it) => (
          <li key={it.id}>
            <button type="button" onClick={() => setOpenId(it.id)}
              className="flex w-full items-start gap-3 rounded-xl p-3 text-left transition-colors hover:bg-[var(--bg-hover)]"
              style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
              <span className="mt-0.5" style={{ color: "var(--text-muted)" }}>
                {it.kind === "phapdien" ? <Library size={18} /> : it.source?.type === "url" ? <Link2 size={18} /> : <FileText size={18} />}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={it.status} />
                  {it.is_vbhn && <Badge>Văn bản hợp nhất</Badge>}
                  {it.so_hieu && <span className="text-sm font-semibold">{it.so_hieu}</span>}
                </span>
                <span className="mt-1 block truncate text-sm" style={{ color: "var(--text-secondary)" }}>
                  {it.ten || it.source?.name}
                </span>
                <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs" style={{ color: "var(--text-muted)" }}>
                  {it.kind === "vanban" && it.so_dieu > 0 && <span>{it.so_dieu} điều</span>}
                  {it.counts && it.kind === "vanban" && (
                    <span>mới {it.counts.new ?? 0} · cập nhật {it.counts.update ?? 0} · không đổi {it.counts.same ?? 0}</span>
                  )}
                  {it.phapdien_summary && (
                    <span>mới {it.phapdien_summary.counts.new} · cập nhật {it.phapdien_summary.counts.update} · không còn {it.phapdien_summary.counts.missing}</span>
                  )}
                  {!!it.warnings && (
                    <span className="inline-flex items-center gap-1" style={{ color: "var(--danger-text)" }}>
                      <AlertTriangle size={12} aria-hidden />{it.warnings} cảnh báo
                    </span>
                  )}
                  <span>{fmtTime(it.created_at)}</span>
                </span>
                {it.error && <span className="mt-1 block text-xs" style={{ color: "var(--danger-text)" }}>{it.error}</span>}
              </span>
              <ChevronRight size={18} className="mt-0.5 shrink-0" style={{ color: "var(--text-muted)" }} aria-hidden />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
