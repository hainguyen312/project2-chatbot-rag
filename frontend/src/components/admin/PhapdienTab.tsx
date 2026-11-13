"use client";
import { useRef, useState } from "react";
import { PhapdienSummary, useAdminFetch } from "./api";
import { Button, Card, Notice } from "./ui";

export function PhapdienTab({ onQueued }: { onQueued: () => void }) {
  const api = useAdminFetch();
  const ref = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [full, setFull] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [res, setRes] = useState<(PhapdienSummary & { id: string }) | null>(null);

  const submit = async () => {
    if (!file) return;
    setBusy(true); setError(""); setRes(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("full", full ? "true" : "false");
      setRes(await api("phapdien", { method: "POST", body: fd }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <p className="mb-3 text-sm" style={{ color: "var(--text-secondary)" }}>
          File JSON hoặc JSONL theo đúng cột của bảng hiện tại. Hệ thống so sánh theo <code>mapc</code> với dữ liệu đang có,
          tạo một mục trong <b>Chờ duyệt</b>; chỉ khi bạn duyệt mới ghi vào kho.
        </p>
        <pre className="mb-3 overflow-x-auto rounded-lg p-3 text-xs" style={{ background: "var(--bg-base)" }}>{`{"dieu":   [{"mapc", "ten", "noidung", "vbqppl", "vbqppl_link", "chuong_id", "demuc_id", "chude_id", "chimuc", "stt"}],
 "chuong": [{"mapc", "ten", "demuc_id", "chimuc", "stt"}],   // tuỳ chọn
 "demuc":  [{"id", "ten", "chude_id", "stt"}],               // tuỳ chọn
 "chude":  [{"id", "ten", "stt"}]}                           // tuỳ chọn`}</pre>
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={() => ref.current?.click()}>Chọn file</Button>
          <span className="min-w-0 truncate text-sm">{file ? `${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)` : "Chưa chọn file"}</span>
          <input ref={ref} type="file" accept=".json,.jsonl,application/json" hidden onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </div>
        <label className="mt-3 flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={full} onChange={(e) => setFull(e.target.checked)} />
          <span>Đây là <b>bản đầy đủ</b> — điều không còn trong bản mới sẽ được đánh dấu hết hiệu lực (không xoá).
            Bỏ chọn nếu file chỉ chứa một phần pháp điển.</span>
        </label>
        <div className="mt-4"><Button variant="primary" onClick={submit} disabled={!file || busy}>{busy ? "Đang so sánh…" : "Tải lên và so sánh"}</Button></div>
      </Card>
      {error && <Notice tone="warn">{error}</Notice>}
      {res && (
        <Card>
          <p className="text-sm">
            Mới <b>{res.counts.new}</b> · cập nhật <b>{res.counts.update}</b> · không đổi <b>{res.counts.same}</b> ·
            không còn <b>{res.counts.missing}</b> · chuyển mã <b>{res.counts.alias}</b>
          </p>
          <div className="mt-3"><Button onClick={onQueued}>Xem và duyệt trong Chờ duyệt</Button></div>
        </Card>
      )}
    </div>
  );
}
