"use client";
import { useRef, useState } from "react";
import { FileUp, Link2, X } from "lucide-react";
import { useAdminFetch } from "./api";
import { Button, Card, Field, Notice, inputCls, inputStyle } from "./ui";

type Created = { id: string | null; name: string; status?: string; error?: string };
const MAX_BATCH = 30;

export function IngestTab({ onQueued }: { onQueued: () => void }) {
  const api = useAdminFetch();
  const [urls, setUrls] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Created[] | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const links = urls.split("\n").map((u) => u.trim()).filter(Boolean);
  const total = links.length + files.length;

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    const pdfs = Array.from(list).filter((f) => f.name.toLowerCase().endsWith(".pdf"));
    setFiles((prev) => [...prev, ...pdfs.filter((f) => !prev.some((p) => p.name === f.name && p.size === f.size))]);
  };

  const submit = async () => {
    setBusy(true); setError(""); setResult(null);
    try {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      fd.append("urls", links.join("\n"));
      const res = await api<{ items: Created[] }>("ingest", { method: "POST", body: fd });
      setResult(res.items);
      setFiles([]); setUrls("");
      if (inputRef.current) inputRef.current.value = "";
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <Field label="Link văn bản (mỗi dòng một link)"
          hint="Trang văn bản trên Công báo (congbao.chinhphu.vn/van-ban/….htm) hoặc link PDF trực tiếp. Chỉ tải khi robots.txt của trang cho phép.">
          <textarea value={urls} onChange={(e) => setUrls(e.target.value)} rows={5} spellCheck={false}
            placeholder={"https://congbao.chinhphu.vn/van-ban/luat-so-15-2026-qh16-470450.htm\nhttps://…/van-ban.pdf"}
            className={`${inputCls} font-mono text-xs`} style={inputStyle} />
        </Field>

        <div className="mt-4">
          <span className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>File PDF</span>
          <div
            className="mt-1 flex flex-col items-center justify-center gap-2 rounded-lg px-4 py-6 text-center text-sm"
            style={{ border: "1px dashed var(--border-strong)", background: "var(--bg-base)", color: "var(--text-secondary)" }}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); addFiles(e.dataTransfer.files); }}
          >
            <FileUp size={20} aria-hidden />
            <span>Kéo thả file PDF vào đây hoặc</span>
            <Button onClick={() => inputRef.current?.click()}>Chọn file</Button>
            <input ref={inputRef} type="file" accept="application/pdf,.pdf" multiple hidden
              onChange={(e) => addFiles(e.target.files)} />
          </div>
          {files.length > 0 && (
            <ul className="mt-2 space-y-1">
              {files.map((f) => (
                <li key={f.name + f.size} className="flex items-center gap-2 text-sm">
                  <FileUp size={14} aria-hidden style={{ color: "var(--text-muted)" }} />
                  <span className="min-w-0 flex-1 truncate">{f.name}</span>
                  <span className="text-xs" style={{ color: "var(--text-muted)" }}>{(f.size / 1024 / 1024).toFixed(1)} MB</span>
                  <button type="button" aria-label={`Bỏ ${f.name}`} onClick={() => setFiles((p) => p.filter((x) => x !== f))}
                    className="rounded p-1 hover:bg-[var(--bg-hover)]"><X size={14} /></button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button variant="primary" onClick={submit} disabled={busy || total === 0 || total > MAX_BATCH}>
            {busy ? "Đang gửi…" : `Đưa ${total || ""} văn bản vào xử lý`}
          </Button>
          <span className="text-xs" style={{ color: total > MAX_BATCH ? "var(--danger-text)" : "var(--text-muted)" }}>
            {links.length} link · {files.length} file · tối đa {MAX_BATCH} mỗi lần
          </span>
        </div>
      </Card>

      {error && <Notice tone="warn">{error}</Notice>}
      {result && (
        <Card>
          <p className="mb-2 text-sm font-medium">Đã nhận {result.filter((r) => r.id).length}/{result.length} văn bản</p>
          <ul className="space-y-1 text-sm">
            {result.map((r, i) => (
              <li key={(r.id ?? "x") + i} className="flex items-center gap-2">
                {r.id ? <Link2 size={14} aria-hidden style={{ color: "var(--ok-text)" }} /> : <X size={14} aria-hidden style={{ color: "var(--danger-text)" }} />}
                <span className="min-w-0 flex-1 truncate">{r.name}</span>
                {r.error && <span className="text-xs" style={{ color: "var(--danger-text)" }}>{r.error}</span>}
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs" style={{ color: "var(--text-muted)" }}>
            Các văn bản được xử lý lần lượt ở nền (trích chữ, tách cấu trúc, so sánh với dữ liệu hiện có).
          </p>
          <div className="mt-3"><Button onClick={onQueued}>Xem trong Chờ duyệt</Button></div>
        </Card>
      )}
    </div>
  );
}
