"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, BookMarked, ClipboardCheck, FileUp, Library, Lock, Moon, ShieldCheck, Sun } from "lucide-react";
import { IngestTab } from "@/components/admin/IngestTab";
import { PhapdienTab } from "@/components/admin/PhapdienTab";
import { ReviewTab } from "@/components/admin/ReviewTab";
import { VanbanTab } from "@/components/admin/VanbanTab";
import { useAuth } from "@/hooks/useAuth";
import { useThemeMode } from "@/hooks/useThemeMode";

type Gate =
  | { state: "checking" }
  | { state: "signed-out" }
  | { state: "denied"; reason: string }
  | { state: "error"; reason: string }
  | { state: "ok"; email: string };

type TabId = "upload" | "review" | "vanban" | "phapdien";

const TABS: { id: TabId; label: string; icon: typeof FileUp; desc: string }[] = [
  { id: "upload", label: "Thêm văn bản", icon: FileUp,
    desc: "Thêm nhiều văn bản một lúc từ link (trang Công báo, link PDF) hoặc file PDF: luật chưa có, văn bản sửa đổi, hợp nhất, thay thế." },
  { id: "review", label: "Chờ duyệt", icon: ClipboardCheck,
    desc: "Kiểm tra cấu trúc đã tách, so sánh với dữ liệu đang có, sửa thông tin rồi duyệt để ghi vào kho tra cứu." },
  { id: "vanban", label: "Văn bản", icon: BookMarked,
    desc: "Sổ cái văn bản và tình trạng hiệu lực — đánh dấu văn bản đã hết hiệu lực, kể cả văn bản có sẵn trong pháp điển." },
  { id: "phapdien", label: "Cập nhật pháp điển", icon: Library,
    desc: "Khi tải được bản pháp điển mới: so sánh theo mapc với dữ liệu hiện có rồi duyệt." },
];

export default function AdminPage() {
  const { user, loading, configured, getIdToken, signInGoogle } = useAuth();
  const [dark, toggleDark] = useThemeMode();
  const [gate, setGate] = useState<Gate>({ state: "checking" });
  const [tab, setTab] = useState<TabId>("upload");

  useEffect(() => {
    if (loading) return;
    let cancelled = false;
    (async () => {
      if (!user) { if (!cancelled) setGate({ state: "signed-out" }); return; }
      const token = await getIdToken();
      const res = await fetch("/api/admin/me", {
        headers: token ? { Authorization: `Bearer ${token}` } : {}, cache: "no-store",
      }).catch(() => null);
      const data = await res?.json().catch(() => ({}));
      if (cancelled) return;
      if (res?.ok) setGate({ state: "ok", email: data.email });
      else if (res && (res.status === 401 || res.status === 403)) setGate({ state: "denied", reason: data?.detail ?? "Không có quyền" });
      else setGate({ state: "error", reason: data?.detail ?? "Không kết nối được backend" });
    })();
    return () => { cancelled = true; };
  }, [user, loading, getIdToken]);

  const active = TABS.find((t) => t.id === tab)!;

  return (
    <div className="flex min-h-screen flex-col" style={{ background: "var(--bg-base)", color: "var(--text-primary)" }}>
      <header className="flex items-center gap-3 px-4 py-3 md:px-6" style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-surface)" }}>
        <Link href="/" className="inline-flex h-9 w-9 items-center justify-center rounded-lg hover:bg-[var(--bg-hover)]" aria-label="Về trang chat">
          <ArrowLeft size={18} />
        </Link>
        <ShieldCheck size={20} style={{ color: "var(--accent)" }} aria-hidden />
        <h1 className="text-base font-semibold">Quản trị dữ liệu pháp luật</h1>
        <div className="ml-auto flex items-center gap-2 text-sm" style={{ color: "var(--text-muted)" }}>
          {gate.state === "ok" && <span className="hidden sm:inline">{gate.email}</span>}
          <button type="button" onClick={toggleDark} aria-label={dark ? "Chuyển nền sáng" : "Chuyển nền tối"}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg hover:bg-[var(--bg-hover)]">
            {dark ? <Sun size={17} /> : <Moon size={17} />}
          </button>
        </div>
      </header>

      {gate.state !== "ok" ? (
        <main className="flex flex-1 items-center justify-center p-4">
          <div className="w-full max-w-sm rounded-xl p-6 text-center" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
            {gate.state === "checking" && <p className="text-sm" style={{ color: "var(--text-muted)" }}>Đang kiểm tra quyền…</p>}
            {gate.state === "signed-out" && (
              <>
                <Lock className="mx-auto mb-3" size={22} style={{ color: "var(--text-muted)" }} aria-hidden />
                <p className="mb-4 text-sm">Trang này chỉ dành cho quản trị viên. Hãy đăng nhập.</p>
                <button type="button" onClick={() => signInGoogle()} disabled={!configured}
                  className="rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-60"
                  style={{ background: "var(--accent)", color: "var(--text-on-accent)" }}>
                  Đăng nhập bằng Google
                </button>
              </>
            )}
            {(gate.state === "denied" || gate.state === "error") && (
              <>
                <Lock className="mx-auto mb-3" size={22} style={{ color: "var(--danger-text)" }} aria-hidden />
                <p className="mb-1 text-sm font-medium">{gate.state === "denied" ? "Không có quyền truy cập" : "Lỗi kiểm tra quyền"}</p>
                <p className="mb-4 text-sm" style={{ color: "var(--text-muted)" }}>{gate.reason}</p>
                <Link href="/" className="text-sm underline" style={{ color: "var(--accent-text)" }}>Về trang chat</Link>
              </>
            )}
          </div>
        </main>
      ) : (
        <div className="flex flex-1 flex-col md:flex-row">
          <nav className="flex gap-1 overflow-x-auto p-3 md:w-60 md:flex-col md:p-4" aria-label="Công cụ"
            style={{ borderRight: "1px solid var(--border)", background: "var(--bg-sidebar)" }}>
            {TABS.map(({ id, label, icon: Icon }) => (
              <button key={id} type="button" onClick={() => setTab(id)} aria-current={tab === id ? "page" : undefined}
                className="inline-flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors hover:bg-[var(--bg-hover)]"
                style={{ background: tab === id ? "var(--bg-surface)" : undefined, fontWeight: tab === id ? 600 : 400,
                  color: tab === id ? "var(--text-primary)" : "var(--text-secondary)" }}>
                <Icon size={16} aria-hidden />{label}
              </button>
            ))}
          </nav>

          <main className="flex-1 p-4 md:p-8">
            <div className="mx-auto max-w-4xl">
              <h2 className="mb-1 text-lg font-semibold">{active.label}</h2>
              <p className="mb-6 text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>{active.desc}</p>
              {tab === "upload" && <IngestTab onQueued={() => setTab("review")} />}
              {tab === "review" && <ReviewTab />}
              {tab === "vanban" && <VanbanTab />}
              {tab === "phapdien" && <PhapdienTab onQueued={() => setTab("review")} />}
            </div>
          </main>
        </div>
      )}
    </div>
  );
}
