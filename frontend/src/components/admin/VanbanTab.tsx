"use client";
import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { TRANG_THAI, useAdminFetch } from "./api";
import { Badge, Button, Card, Field, Notice, fmtTime, inputCls, inputStyle } from "./ui";

type Vanban = {
  so_hieu: string; loai: string | null; trich_yeu: string | null; trang_thai: string; hieu_luc_tu: string | null;
  hieu_luc_den: string | null; thay_the_boi: string | null; is_vbhn: number; goc_so_hieu: string | null;
  nguon: string | null; cap_nhat_luc: string | null;
};

export function VanbanTab() {
  const api = useAdminFetch();
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<Vanban[] | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const [form, setForm] = useState({ so_hieu: "", trang_thai: "het_hieu_luc", hieu_luc_den: "", thay_the_boi: "" });
  const [msg, setMsg] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    api<{ items: Vanban[] }>(`vanban?q=${encodeURIComponent(query)}`)
      .then((r) => { if (!cancelled) { setItems(r.items); setError(""); } })
      .catch((e) => { if (!cancelled) setError((e as Error).message); });
    return () => { cancelled = true; };
  }, [api, query, tick]);

  const save = async () => {
    setMsg(null);
    try {
      await api("vanban/status", { method: "POST", body: JSON.stringify(form) });
      setMsg({ tone: "ok", text: `Đã cập nhật ${form.so_hieu}: ${TRANG_THAI[form.trang_thai]}.` });
      setTick((t) => t + 1);
    } catch (e) {
      setMsg({ tone: "warn", text: (e as Error).message });
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <h3 className="mb-1 text-sm font-semibold">Đặt tình trạng hiệu lực</h3>
        <p className="mb-3 text-xs" style={{ color: "var(--text-muted)" }}>
          Áp dụng cho mọi điều trích từ văn bản này (kể cả dữ liệu pháp điển cũ). Văn bản hết hiệu lực bị hạ điểm khi tra cứu
          và agent sẽ nói rõ văn bản thay thế. Ví dụ: 256/2016/TT-BTC → hết hiệu lực một phần, thay bởi 59/2019/TT-BTC.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Số hiệu"><input value={form.so_hieu} onChange={(e) => setForm({ ...form, so_hieu: e.target.value })} className={inputCls} style={inputStyle} placeholder="256/2016/TT-BTC" /></Field>
          <Field label="Tình trạng">
            <select value={form.trang_thai} onChange={(e) => setForm({ ...form, trang_thai: e.target.value })} className={inputCls} style={inputStyle}>
              {Object.entries(TRANG_THAI).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </Field>
          <Field label="Hết hiệu lực từ (tuỳ chọn)"><input type="date" value={form.hieu_luc_den} onChange={(e) => setForm({ ...form, hieu_luc_den: e.target.value })} className={inputCls} style={inputStyle} /></Field>
          <Field label="Văn bản thay thế (tuỳ chọn)"><input value={form.thay_the_boi} onChange={(e) => setForm({ ...form, thay_the_boi: e.target.value })} className={inputCls} style={inputStyle} placeholder="59/2019/TT-BTC" /></Field>
        </div>
        <div className="mt-3"><Button variant="primary" onClick={save} disabled={!form.so_hieu.trim()}>Lưu</Button></div>
        {msg && <div className="mt-3"><Notice tone={msg.tone}>{msg.text}</Notice></div>}
      </Card>

      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm theo số hiệu hoặc trích yếu" className={inputCls} style={inputStyle} />
        <Button type="submit"><Search size={14} aria-hidden />Tìm</Button>
      </form>
      {error && <Notice tone="warn">{error}</Notice>}
      {items?.length === 0 && <p className="text-sm" style={{ color: "var(--text-muted)" }}>Sổ cái chưa có văn bản nào{query && " khớp"}.</p>}
      <ul className="space-y-2">
        {items?.map((v) => (
          <li key={v.so_hieu}>
            <button type="button" onClick={() => setForm({ so_hieu: v.so_hieu, trang_thai: v.trang_thai, hieu_luc_den: v.hieu_luc_den ?? "", thay_the_boi: v.thay_the_boi ?? "" })}
              className="w-full rounded-xl p-3 text-left hover:bg-[var(--bg-hover)]" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
              <span className="flex flex-wrap items-center gap-2 text-sm">
                <b>{v.so_hieu}</b>
                <Badge tone={v.trang_thai === "het_hieu_luc" ? "warn" : v.trang_thai === "con_hieu_luc" ? "ok" : "muted"}>{TRANG_THAI[v.trang_thai] ?? v.trang_thai}</Badge>
                {!!v.is_vbhn && <Badge>VBHN của {v.goc_so_hieu}</Badge>}
              </span>
              <span className="mt-1 block truncate text-sm" style={{ color: "var(--text-secondary)" }}>{[v.loai, v.trich_yeu].filter(Boolean).join(" ")}</span>
              <span className="mt-1 block text-xs" style={{ color: "var(--text-muted)" }}>
                {v.hieu_luc_tu && `Hiệu lực ${v.hieu_luc_tu}`}{v.hieu_luc_den && ` → ${v.hieu_luc_den}`}
                {v.thay_the_boi && ` · thay bởi ${v.thay_the_boi}`}{v.nguon && ` · nguồn ${v.nguon}`} · {fmtTime(v.cap_nhat_luc)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
