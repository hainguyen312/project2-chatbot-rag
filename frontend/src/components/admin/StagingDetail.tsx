"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeft, Check, ChevronDown, Plus, RefreshCw, X } from "lucide-react";
import { BUSY, RELATION, StagingDetail, useAdminFetch } from "./api";
import { Badge, Button, Card, Field, Notice, StatusBadge, fmtTime, inputCls, inputStyle } from "./ui";

type Demuc = { id: string; ten: string; chude_id: string; chude: string };

export function StagingDetailView({ id, onBack }: { id: string; onBack: () => void }) {
  const api = useAdminFetch();
  const [item, setItem] = useState<StagingDetail | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    api<StagingDetail>(`staging/${id}`)
      .then((r) => { if (!cancelled) { setItem(r); setError(""); } })
      .catch((e) => { if (!cancelled) setError((e as Error).message); });
    return () => { cancelled = true; };
  }, [api, id, tick]);

  const busy = !!item && BUSY.has(item.status);
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(reload, 2500);
    return () => clearInterval(t);
  }, [busy, reload]);

  return (
    <div className="space-y-4">
      <button type="button" onClick={onBack} className="inline-flex items-center gap-1.5 text-sm hover:underline"
        style={{ color: "var(--text-secondary)" }}>
        <ArrowLeft size={15} aria-hidden />Danh sách
      </button>
      {error && <Notice tone="warn">{error}</Notice>}
      {!item && !error && <p className="text-sm" style={{ color: "var(--text-muted)" }}>Đang tải…</p>}
      {/* key: dữ liệu mới từ server → khởi tạo lại form */}
      {item && <DetailBody key={item.updated_at} item={item} onChanged={reload} onBack={onBack} />}
    </div>
  );
}

function DetailBody({ item, onChanged, onBack }: { item: StagingDetail; onChanged: () => void; onBack: () => void }) {
  const api = useAdminFetch();
  const doc = item.doc;
  const editable = item.kind === "vanban" && (item.status === "ready" || item.status === "error") && !!doc;
  const [form, setForm] = useState(() => ({
    so_hieu: doc?.so_hieu ?? "", loai: doc?.loai ?? "", trich_yeu: doc?.trich_yeu ?? "", co_quan: doc?.co_quan ?? "",
    ngay_ban_hanh: doc?.ngay_ban_hanh ?? "", hieu_luc_tu: doc?.hieu_luc_tu ?? "", is_vbhn: !!doc?.is_vbhn,
    goc_so_hieu: doc?.goc_so_hieu ?? "", demuc_id: item.demuc_id ?? "",
  }));
  const [rels, setRels] = useState(() => doc?.relations ?? []);
  const [newRel, setNewRel] = useState({ loai: "sua_doi", so_hieu: "" });
  const [added, setAdded] = useState<{ loai: string; so_hieu: string }[]>([]);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);
  const [demuc, setDemuc] = useState<Demuc[] | null>(null);

  useEffect(() => {
    if (!editable) return;
    let cancelled = false;
    api<{ items: Demuc[] }>("demuc").then((r) => { if (!cancelled) setDemuc(r.items); }).catch(() => {});
    return () => { cancelled = true; };
  }, [api, editable]);

  const dirty = useMemo(() => {
    if (!doc) return false;
    const base = { so_hieu: doc.so_hieu, loai: doc.loai, trich_yeu: doc.trich_yeu, co_quan: doc.co_quan,
      ngay_ban_hanh: doc.ngay_ban_hanh, hieu_luc_tu: doc.hieu_luc_tu, is_vbhn: !!doc.is_vbhn,
      goc_so_hieu: doc.goc_so_hieu, demuc_id: item.demuc_id ?? "" };
    return JSON.stringify(base) !== JSON.stringify(form) || added.length > 0
      || rels.some((r, i) => r.xac_nhan !== doc.relations[i]?.xac_nhan);
  }, [doc, form, rels, added, item.demuc_id]);

  const act = async (label: string, fn: () => Promise<unknown>, okText: string) => {
    setBusy(label); setMsg(null);
    try { await fn(); setMsg({ tone: "ok", text: okText }); onChanged(); }
    catch (e) { setMsg({ tone: "warn", text: (e as Error).message }); }
    finally { setBusy(""); }
  };

  const save = () => act("save", () => {
    const d = demuc?.find((x) => x.id === form.demuc_id);
    return api(`staging/${item.id}`, { method: "PATCH", body: JSON.stringify({
      ...form, chude_id: d?.chude_id ?? "", relations: rels.map(({ loai, so_hieu, xac_nhan }) => ({ loai, so_hieu, xac_nhan })),
      new_relations: added,
    }) });
  }, "Đã lưu và so sánh lại với dữ liệu hiện có.");

  const approve = () => act("approve", () => api(`staging/${item.id}/approve`, { method: "POST" }),
    "Đang ghi vào kho tra cứu…");
  const reject = () => act("reject", () => api(`staging/${item.id}/reject`, { method: "POST" }), "Đã từ chối.");
  const retry = () => act("retry", () => api(`staging/${item.id}/retry`, { method: "POST" }), "Đã đưa vào xử lý lại.");

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value }));

  const src = item.source || {};
  const counts = item.diff?.counts ?? {};

  return (
    <>
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={item.status} />
          {doc?.is_vbhn && <Badge>Văn bản hợp nhất</Badge>}
          <h2 className="text-base font-semibold">{doc?.ten_day_du || item.ten || src.name}</h2>
        </div>
        <p className="mt-1 break-all text-xs" style={{ color: "var(--text-muted)" }}>
          {src.type === "url" ? "Link" : "File"}: {src.url ? <a href={src.url} target="_blank" rel="noreferrer" className="underline">{src.url}</a> : src.name}
          {src.pdf_url && src.pdf_url !== src.url && <> · <a href={src.pdf_url} target="_blank" rel="noreferrer" className="underline">PDF</a></>}
          {" "}· {item.created_by} · {fmtTime(item.created_at)}
          {item.extract && <> · {item.extract.pages} trang</>}
        </p>
        {item.error && <div className="mt-3"><Notice tone="warn">{item.error}</Notice></div>}
        {!!doc?.warnings?.length && (
          <ul className="mt-3 space-y-1">
            {doc.warnings.map((w) => (
              <li key={w} className="flex items-start gap-2 text-sm" style={{ color: "var(--danger-text)" }}>
                <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />{w}
              </li>
            ))}
          </ul>
        )}
        {item.publish_report && (
          <div className="mt-3"><Notice tone="ok">
            Đã ghi {String(item.publish_report.changed ?? 0)} điều
            {item.publish_report.embedded != null && <>, embed {String(item.publish_report.embedded)} điều</>}
            {Array.isArray(item.publish_report.relations_applied) && item.publish_report.relations_applied.length > 0 &&
              <>, áp dụng {item.publish_report.relations_applied.length} quan hệ</>}. Chỉ mục BM25 đang build lại ở nền.
          </Notice></div>
        )}
        {busy !== "" && <p className="mt-3 text-sm" style={{ color: "var(--text-muted)" }}>Đang thực hiện…</p>}
        {msg && <div className="mt-3"><Notice tone={msg.tone}>{msg.text}</Notice></div>}

        <div className="mt-4 flex flex-wrap gap-2">
          {editable && <Button onClick={save} disabled={!dirty || !!busy}>Lưu thay đổi</Button>}
          {item.status === "ready" && (
            <Button variant="primary" onClick={approve} disabled={dirty || !!busy}
              title={dirty ? "Lưu thay đổi trước khi duyệt" : undefined}>
              <Check size={15} aria-hidden />Duyệt và ghi vào kho
            </Button>
          )}
          {(item.status === "ready" || item.status === "error") && (
            <Button variant="danger" onClick={() => { reject(); }} disabled={!!busy}><X size={15} aria-hidden />Từ chối</Button>
          )}
          {["error", "publish_error", "rejected"].includes(item.status) && (
            <Button onClick={retry} disabled={!!busy}><RefreshCw size={14} aria-hidden />{item.status === "publish_error" ? "Thử ghi lại" : "Xử lý lại"}</Button>
          )}
          {item.status === "published" && <Button onClick={onBack}>Xong</Button>}
        </div>
      </Card>

      {item.kind === "phapdien" && item.phapdien_summary && <PhapdienSummaryView s={item.phapdien_summary} />}

      {doc && item.kind === "vanban" && (
        <>
          <Card>
            <h3 className="mb-3 text-sm font-semibold">Thông tin văn bản</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Số hiệu"><input value={form.so_hieu} onChange={set("so_hieu")} disabled={!editable} className={inputCls} style={inputStyle} /></Field>
              <Field label="Loại văn bản"><input value={form.loai} onChange={set("loai")} disabled={!editable} className={inputCls} style={inputStyle} placeholder="Luật, Nghị định, Thông tư…" /></Field>
              <div className="sm:col-span-2"><Field label="Trích yếu"><input value={form.trich_yeu} onChange={set("trich_yeu")} disabled={!editable} className={inputCls} style={inputStyle} /></Field></div>
              <Field label="Cơ quan ban hành"><input value={form.co_quan} onChange={set("co_quan")} disabled={!editable} className={inputCls} style={inputStyle} /></Field>
              <Field label="Ngày ban hành"><input type="date" value={form.ngay_ban_hanh} onChange={set("ngay_ban_hanh")} disabled={!editable} className={inputCls} style={inputStyle} /></Field>
              <Field label="Có hiệu lực từ"><input type="date" value={form.hieu_luc_tu} onChange={set("hieu_luc_tu")} disabled={!editable} className={inputCls} style={inputStyle} /></Field>
              <Field label="Đề mục pháp điển (tuỳ chọn)" hint="Để trống nếu chưa rõ; cấu trúc văn bản được giữ nguyên.">
                <select value={form.demuc_id} onChange={set("demuc_id")} disabled={!editable} className={inputCls} style={inputStyle}>
                  <option value="">— Không gán —</option>
                  {demuc?.map((d) => <option key={d.id} value={d.id}>{d.chude} › {d.ten}</option>)}
                </select>
              </Field>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={form.is_vbhn} onChange={set("is_vbhn")} disabled={!editable} />Văn bản hợp nhất
              </label>
              {form.is_vbhn && (
                <Field label="Số hiệu văn bản gốc" hint="Các điều sẽ cập nhật vào văn bản gốc này.">
                  <input value={form.goc_so_hieu} onChange={set("goc_so_hieu")} disabled={!editable} className={inputCls} style={inputStyle} />
                </Field>
              )}
            </div>
          </Card>

          <Card>
            <h3 className="mb-1 text-sm font-semibold">Quan hệ với văn bản khác</h3>
            <p className="mb-3 text-xs" style={{ color: "var(--text-muted)" }}>
              Gợi ý tự động từ nội dung — chỉ quan hệ được tích mới áp dụng khi duyệt. &quot;Bãi bỏ&quot; / &quot;Làm hết hiệu lực&quot;
              sẽ đánh dấu văn bản đó hết hiệu lực trong kho tra cứu.
            </p>
            {rels.length === 0 && added.length === 0 && <p className="text-sm" style={{ color: "var(--text-muted)" }}>Không tìm thấy quan hệ nào.</p>}
            {rels.length > 3 && editable && (
              <div className="mb-2 flex gap-3 text-xs">
                <button type="button" className="underline" onClick={() => setRels((r) => r.map((x) => ({ ...x, xac_nhan: true })))}>Chọn tất cả</button>
                <button type="button" className="underline" onClick={() => setRels((r) => r.map((x) => ({ ...x, xac_nhan: false })))}>Bỏ chọn</button>
              </div>
            )}
            <ul className="space-y-2">
              {rels.map((r, i) => (
                <li key={r.loai + r.so_hieu} className="flex items-start gap-2 text-sm">
                  <input type="checkbox" className="mt-1" checked={r.xac_nhan} disabled={!editable}
                    onChange={(e) => setRels((p) => p.map((x, j) => (j === i ? { ...x, xac_nhan: e.target.checked } : x)))} />
                  <span className="min-w-0 flex-1">
                    <span className="font-medium">{RELATION[r.loai] ?? r.loai}</span> <span className="font-semibold">{r.so_hieu}</span>
                    {r.trich && <span className="mt-0.5 block text-xs" style={{ color: "var(--text-muted)" }}>{r.trich}</span>}
                  </span>
                </li>
              ))}
              {added.map((r) => (
                <li key={"n" + r.loai + r.so_hieu} className="flex items-center gap-2 text-sm">
                  <Check size={14} aria-hidden /> {RELATION[r.loai]} <b>{r.so_hieu}</b> <Badge>thêm tay</Badge>
                  <button type="button" aria-label="Bỏ" onClick={() => setAdded((a) => a.filter((x) => x !== r))}><X size={14} /></button>
                </li>
              ))}
            </ul>
            {editable && (
              <div className="mt-3 flex flex-wrap items-end gap-2">
                <select value={newRel.loai} onChange={(e) => setNewRel((n) => ({ ...n, loai: e.target.value }))} className={`${inputCls} w-auto`} style={inputStyle}>
                  {Object.entries(RELATION).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
                <input value={newRel.so_hieu} onChange={(e) => setNewRel((n) => ({ ...n, so_hieu: e.target.value }))}
                  placeholder="Số hiệu, vd. 59/2014/QH13" className={`${inputCls} w-56`} style={inputStyle} />
                <Button onClick={() => { if (newRel.so_hieu.trim()) { setAdded((a) => [...a, { ...newRel, so_hieu: newRel.so_hieu.trim() }]); setNewRel((n) => ({ ...n, so_hieu: "" })); } }}>
                  <Plus size={14} aria-hidden />Thêm
                </Button>
              </div>
            )}
          </Card>

          <Card>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-semibold">{doc.articles.length} điều</h3>
              {item.diff?.target && (
                <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                  so với {item.diff.existing ?? 0} điều của {item.diff.target} đang có trong kho:
                </span>
              )}
              <Badge tone="accent">mới {counts.new ?? 0}</Badge>
              <Badge tone="warn">cập nhật {counts.update ?? 0}</Badge>
              <Badge>không đổi {counts.same ?? 0}</Badge>
            </div>
            {item.diff?.error && <Notice tone="warn">{item.diff.error}</Notice>}
            {!!item.diff?.not_in_doc?.length && (
              <p className="mb-2 text-xs" style={{ color: "var(--text-muted)" }}>
                Điều có trong kho nhưng không có trong văn bản này (giữ nguyên): {item.diff.not_in_doc.slice(0, 20).join(", ")}
              </p>
            )}
            <ArticleList item={item} />
          </Card>
        </>
      )}
    </>
  );
}

function ArticleList({ item }: { item: StagingDetail }) {
  const [open, setOpen] = useState<string | null>(null);
  const [only, setOnly] = useState<"all" | "new" | "update">("all");
  const doc = item.doc!;
  const byNo = new Map((item.diff?.items ?? []).map((d) => [d.so, d]));
  const visible = doc.articles.filter((a) => only === "all" || byNo.get(a.so)?.action === only);
  const groupOf = (a: (typeof visible)[number]) => [a.phan, a.chuong, a.muc].filter(Boolean).join(" › ");
  return (
    <>
      <div className="mb-2 flex gap-2 text-xs">
        {(["all", "new", "update"] as const).map((k) => (
          <button key={k} type="button" onClick={() => setOnly(k)} className="rounded-full px-2.5 py-0.5"
            style={{ background: only === k ? "var(--bg-hover)" : undefined, fontWeight: only === k ? 600 : 400 }}>
            {k === "all" ? "Tất cả" : k === "new" ? "Điều mới" : "Điều thay đổi"}
          </button>
        ))}
      </div>
      <ul className="divide-y" style={{ borderColor: "var(--border)" }}>
        {visible.map((a, i) => {
          const d = byNo.get(a.so);
          const group = groupOf(a);
          const showGroup = group && (i === 0 || group !== groupOf(visible[i - 1]));
          const isOpen = open === a.so;
          return (
            <li key={a.so} style={{ borderColor: "var(--border)" }}>
              {showGroup && <div className="pb-1 pt-3 text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>{group}</div>}
              <button type="button" onClick={() => setOpen(isOpen ? null : a.so)} aria-expanded={isOpen}
                className="flex w-full items-center gap-2 py-2 text-left text-sm hover:bg-[var(--bg-hover)]">
                <ChevronDown size={14} aria-hidden style={{ transform: isOpen ? "none" : "rotate(-90deg)", transition: "transform .15s", color: "var(--text-muted)" }} />
                <span className="min-w-0 flex-1 truncate"><b>Điều {a.so}.</b> {a.ten}</span>
                {d && <Badge tone={d.action === "new" ? "accent" : d.action === "update" ? "warn" : "muted"}>
                  {d.action === "new" ? "mới" : d.action === "update" ? "cập nhật" : "không đổi"}
                </Badge>}
              </button>
              {isOpen && (
                <div className="grid gap-3 pb-3 pl-6 md:grid-cols-2">
                  {d?.action === "update" && (
                    <div>
                      <div className="mb-1 text-xs font-medium" style={{ color: "var(--text-muted)" }}>Đang có ({d.old_ten})</div>
                      <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg p-2 text-xs leading-relaxed"
                        style={{ background: "var(--danger-bg)", fontFamily: "inherit" }}>{d.old_noidung}</pre>
                    </div>
                  )}
                  <div className={d?.action === "update" ? "" : "md:col-span-2"}>
                    {d?.action === "update" && <div className="mb-1 text-xs font-medium" style={{ color: "var(--text-muted)" }}>Từ văn bản này</div>}
                    <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg p-2 text-xs leading-relaxed"
                      style={{ background: d?.action === "update" ? "var(--ok-bg)" : "var(--bg-base)", fontFamily: "inherit" }}>{a.noidung}</pre>
                    <div className="mt-1 text-[11px]" style={{ color: "var(--text-muted)" }}>mapc: {d?.mapc}</div>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </>
  );
}

function PhapdienSummaryView({ s }: { s: NonNullable<StagingDetail["phapdien_summary"]> }) {
  const rows: [string, string, { mapc?: string; ten?: string; alias?: string }[]][] = [
    ["new", "Điều mới", s.sample.new],
    ["update", "Điều thay đổi nội dung (bản cũ được lưu lịch sử)", s.sample.update],
    ["missing", "Không còn trong bản mới → đánh dấu hết hiệu lực", s.sample.missing],
    ["alias", "Điều đã thêm từ văn bản nay có trong pháp điển → chuyển sang mapc pháp điển", s.sample.alias],
  ];
  return (
    <Card>
      <h3 className="mb-2 text-sm font-semibold">So sánh với dữ liệu pháp điển hiện có</h3>
      <p className="mb-3 text-xs" style={{ color: "var(--text-muted)" }}>
        {s.full ? "Bản đầy đủ: điều không còn trong bản mới sẽ bị đánh dấu hết hiệu lực (không xoá)." : "Bản cập nhật một phần: không đánh dấu điều nào hết hiệu lực."}
        {" "}Không đổi: {s.counts.same ?? 0} điều.
      </p>
      <div className="space-y-3">
        {rows.map(([k, label, list]) => (
          <details key={k}>
            <summary className="cursor-pointer text-sm"><b>{(s.counts as Record<string, number>)[k] ?? 0}</b> — {label}</summary>
            <ul className="mt-1 space-y-0.5 pl-4 text-xs" style={{ color: "var(--text-secondary)" }}>
              {list.map((x, i) => <li key={i}>{x.alias ? `${x.alias} → ${x.mapc}` : `${x.ten} (${x.mapc})`}</li>)}
              {list.length === 0 && <li>—</li>}
            </ul>
          </details>
        ))}
      </div>
    </Card>
  );
}
