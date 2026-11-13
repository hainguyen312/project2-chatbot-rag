"use client";
import { useCallback } from "react";
import { useAuth } from "@/hooks/useAuth";

/** Gọi /api/admin/* kèm token Firebase; lỗi → throw Error(detail của backend). */
export function useAdminFetch() {
  const { getIdToken } = useAuth();
  return useCallback(
    async <T = unknown>(path: string, init: RequestInit = {}): Promise<T> => {
      const token = await getIdToken();
      const headers = new Headers(init.headers);
      if (token) headers.set("Authorization", `Bearer ${token}`);
      if (init.body && typeof init.body === "string") headers.set("Content-Type", "application/json");
      const res = await fetch(`/api/admin/${path}`, { ...init, headers, cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const d = (data as { detail?: unknown }).detail;
        throw new Error(typeof d === "string" ? d : `Lỗi ${res.status}`);
      }
      return data as T;
    },
    [getIdToken],
  );
}

export type Counts = { new?: number; update?: number; same?: number; missing?: number; alias?: number };

export type StagingSummary = {
  id: string;
  kind: "vanban" | "phapdien";
  status: string;
  so_hieu: string | null;
  ten: string | null;
  so_dieu: number;
  source: { type: string; name?: string; url?: string; pdf_url?: string; nguon?: string };
  error: string | null;
  created_by: string | null;
  approved_by?: string | null;
  created_at: string;
  updated_at: string;
  warnings: number | null;
  counts: Counts | null;
  is_vbhn: boolean;
  phapdien_summary?: PhapdienSummary | null;
};

export type Relation = { loai: string; so_hieu: string; trich: string; xac_nhan: boolean };
export type Article = { so: string; ten: string; noidung: string; phan: string; chuong: string; muc: string };
export type DiffItem = { so: string; action: "new" | "update" | "same"; mapc: string; old_ten: string | null; old_noidung: string | null };

export type StagingDetail = Omit<StagingSummary, "warnings" | "counts"> & {
  doc: {
    so_hieu: string; loai: string; trich_yeu: string; co_quan: string; ngay_ban_hanh: string; hieu_luc_tu: string;
    is_vbhn: boolean; goc_so_hieu: string; ten_day_du: string; articles: Article[]; relations: Relation[];
    warnings: string[]; errors: string[];
  } | null;
  diff: { target?: string; existing?: number; counts: Counts; items: DiffItem[]; not_in_doc?: string[]; error?: string } | null;
  extract: { pages: number; scanned_pages: number[]; ocr_pages: number[] } | null;
  demuc_id: string;
  chude_id: string;
  publish_report: Record<string, unknown> | null;
};

export type PhapdienSummary = {
  full: boolean;
  counts: Counts;
  sample: { new: { mapc: string; ten: string }[]; update: { mapc: string; ten: string }[];
    missing: { mapc: string; ten: string }[]; alias: { alias: string; mapc: string }[] };
};

export const STATUS: Record<string, { label: string; tone: "muted" | "ok" | "warn" | "accent" }> = {
  queued: { label: "Đang chờ", tone: "muted" },
  processing: { label: "Đang xử lý", tone: "muted" },
  ready: { label: "Chờ duyệt", tone: "accent" },
  error: { label: "Lỗi", tone: "warn" },
  publishing: { label: "Đang ghi", tone: "muted" },
  published: { label: "Đã duyệt", tone: "ok" },
  publish_error: { label: "Lỗi khi ghi", tone: "warn" },
  rejected: { label: "Đã từ chối", tone: "muted" },
};
export const BUSY = new Set(["queued", "processing", "publishing"]);

export const RELATION: Record<string, string> = {
  sua_doi: "Sửa đổi, bổ sung",
  bai_bo: "Bãi bỏ",
  het_hieu_luc: "Làm hết hiệu lực",
  hop_nhat: "Hợp nhất từ",
};

export const TRANG_THAI: Record<string, string> = {
  con_hieu_luc: "Còn hiệu lực",
  chua_hieu_luc: "Chưa có hiệu lực",
  het_hieu_luc_mot_phan: "Hết hiệu lực một phần",
  het_hieu_luc: "Hết hiệu lực",
};
