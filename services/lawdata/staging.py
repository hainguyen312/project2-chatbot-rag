"""
Khu chờ duyệt (bảng MySQL `law_staging`, cùng DB với dữ liệu luật) + hàng đợi xử lý nền.

Mỗi file/link = một mục. Trạng thái:
  queued → processing → ready | error
  ready → publishing → published | publish_error
  ready → rejected
Chatbot chỉ đọc dữ liệu đã duyệt (published); khu chờ duyệt không ảnh hưởng tra cứu.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import traceback
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from services.lawdata import store
from services.lawdata.parser import parse_document
from services.lawdata.pdf_extract import extract_pdf
from services.lawdata.sources import SourceError, fetch_url

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("LAWDATA_DIR", str(ROOT / ".cache" / "lawdata")))
PDF_DIR = DATA_DIR / "pdf"
MAX_BATCH = 30

JSON_COLS = ("source", "doc", "diff", "extract", "hints", "publish_report", "phapdien_summary")
TABLE_SQL = """CREATE TABLE IF NOT EXISTS law_staging (
    id VARCHAR(40) PRIMARY KEY,
    kind VARCHAR(16) NOT NULL DEFAULT 'vanban',
    status VARCHAR(20) NOT NULL,
    so_hieu VARCHAR(80) NULL,
    ten TEXT NULL,
    so_dieu INT NOT NULL DEFAULT 0,
    source JSON, doc LONGTEXT, diff LONGTEXT, extract JSON, hints JSON,
    publish_report JSON, phapdien_summary JSON,
    demuc_id VARCHAR(128) NOT NULL DEFAULT '', chude_id VARCHAR(128) NOT NULL DEFAULT '',
    error TEXT NULL, created_by VARCHAR(255), approved_by VARCHAR(255),
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, published_at DATETIME NULL,
    KEY ix_status (status, created_at), KEY ix_so_hieu (so_hieu)
) CHARACTER SET utf8mb4"""

_q: "queue.Queue[tuple[str, str]]" = queue.Queue()
_worker: Optional[threading.Thread] = None
_lock = threading.Lock()
_table_ready = False


def _now() -> datetime:
    return datetime.now()


def _conn():
    global _table_ready
    conn = store.connect()
    if not _table_ready:
        with conn.cursor() as cur:
            cur.execute(TABLE_SQL)
        conn.commit()
        _table_ready = True
    return conn


def _row_out(r: dict) -> dict:
    for k in JSON_COLS:
        if isinstance(r.get(k), (str, bytes)):
            r[k] = json.loads(r[k])
    for k, v in r.items():
        if isinstance(v, (date, datetime)):
            r[k] = v.isoformat()
    return r


def get_row(iid: str, cols: str = "*") -> Optional[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {cols} FROM law_staging WHERE id=%s", (iid,))
            r = cur.fetchone()
    finally:
        conn.close()
    return _row_out(r) if r else None


def _set(iid: str, where_status: Optional[str] = None, **fields) -> int:
    """Cập nhật một mục; where_status → chỉ cập nhật khi đang ở trạng thái đó (trả số dòng đổi)."""
    fields["updated_at"] = _now()
    doc = fields.get("doc")
    if isinstance(doc, dict):
        fields.setdefault("so_hieu", doc.get("so_hieu") or None)
        fields.setdefault("ten", doc.get("ten_day_du"))
        fields.setdefault("so_dieu", len(doc.get("articles") or []))
    cols, vals = [], []
    for k, v in fields.items():
        cols.append(f"{k}=%s")
        vals.append(json.dumps(v, ensure_ascii=False, default=str) if k in JSON_COLS and v is not None else v)
    sql = f"UPDATE law_staging SET {', '.join(cols)} WHERE id=%s"
    vals.append(iid)
    if where_status:
        sql += " AND status=%s"
        vals.append(where_status)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            n = cur.execute(sql, vals)
        conn.commit()
        return n
    finally:
        conn.close()


def insert_row(iid: str, kind: str, status: str, source: dict, actor: str, **extra) -> None:
    now = _now()
    row = {"id": iid, "kind": kind, "status": status, "source": source, "created_by": actor,
           "created_at": now, "updated_at": now, "ten": source.get("name"), **extra}
    cols = list(row)
    vals = [json.dumps(v, ensure_ascii=False, default=str) if k in JSON_COLS and v is not None else v
            for k, v in row.items()]
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO law_staging ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", vals)
        conn.commit()
    finally:
        conn.close()


# ── Tạo mục ──────────────────────────────────────────────────────────────────
def create_items(files: list[tuple[str, bytes]], urls: list[str], actor: str) -> list[dict]:
    urls = [u.strip() for u in urls if u.strip()]
    if not files and not urls:
        raise ValueError("Chưa có file hoặc link nào")
    if len(files) + len(urls) > MAX_BATCH:
        raise ValueError(f"Tối đa {MAX_BATCH} văn bản mỗi lần")
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    created = []
    for name, data in files:
        if not data.startswith(b"%PDF"):
            created.append({"id": None, "name": name, "error": "Không phải file PDF"})
            continue
        iid = uuid.uuid4().hex
        (PDF_DIR / f"{iid}.pdf").write_bytes(data)
        insert_row(iid, "vanban", "queued", {"type": "file", "name": name}, actor)
        created.append({"id": iid, "name": name, "status": "queued"})
    for u in urls:
        iid = uuid.uuid4().hex
        insert_row(iid, "vanban", "queued", {"type": "url", "url": u, "name": u}, actor)
        created.append({"id": iid, "name": u, "status": "queued"})
    for c in created:
        if c.get("id"):
            enqueue("process", c["id"])
    return created


# ── Hàng đợi ─────────────────────────────────────────────────────────────────
def enqueue(job: str, item_id: str) -> None:
    _ensure_worker()
    _q.put((job, item_id))


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, name="lawdata-worker", daemon=True)
            _worker.start()


def _loop() -> None:
    while True:
        job, iid = _q.get()
        try:
            if job == "process":
                process(iid)
            elif job == "publish":
                publish(iid)
            elif job == "publish_phapdien":
                from services.lawdata.phapdien import apply as apply_phapdien

                apply_phapdien(iid)
        except Exception as e:  # không để worker chết
            print(f"[Lawdata] job {job} {iid} lỗi: {e}")
            traceback.print_exc()
        finally:
            _q.task_done()


def resume_pending() -> int:
    """Gọi khi backend khởi động: xử lý tiếp các mục đang dở."""
    try:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, kind, status FROM law_staging "
                            "WHERE status IN ('queued','processing','publishing') ORDER BY created_at")
                rows = cur.fetchall()
        finally:
            conn.close()
        for r in rows:
            if r["status"] == "publishing":
                enqueue("publish_phapdien" if r["kind"] == "phapdien" else "publish", r["id"])
            elif r["kind"] == "vanban":
                enqueue("process", r["id"])
        return len(rows)
    except Exception as e:
        print(f"[Lawdata] Không khôi phục được hàng đợi: {e}")
        return 0


# ── Xử lý: PDF → khu chờ duyệt ───────────────────────────────────────────────
def process(iid: str) -> None:
    item = get_row(iid)
    if not item:
        return
    _set(iid, status="processing", error=None)
    try:
        src = dict(item["source"])
        path = PDF_DIR / f"{iid}.pdf"
        hints: dict = {}
        if src["type"] == "url":
            fetched = fetch_url(src["url"])
            PDF_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(fetched.pdf)
            hints = fetched.hints
            src.update(pdf_url=fetched.pdf_url, nguon=hints.get("nguon"))
        else:
            src["nguon"] = "upload"
        ex = extract_pdf(path.read_bytes())
        parsed = parse_document(ex.text, hints).to_dict()
        parsed["warnings"] = ex.warnings + parsed["warnings"]
        if parsed["so_hieu"] and _other_pending(iid, parsed["so_hieu"]):
            parsed["warnings"].append(f"Một mục khác đang chờ duyệt cùng số hiệu {parsed['so_hieu']}.")
        _set(iid, status="error" if parsed["errors"] else "ready", source=src, doc=parsed, diff=_diff(parsed),
             extract={"pages": ex.pages, "scanned_pages": ex.scanned_pages, "ocr_pages": ex.ocr_pages},
             hints=hints, error="; ".join(parsed["errors"]) or None)
    except SourceError as e:
        _set(iid, status="error", error=str(e))
    except Exception as e:
        traceback.print_exc()
        _set(iid, status="error", error=f"Lỗi xử lý: {e}")


def _other_pending(iid: str, so_hieu: str) -> bool:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM law_staging WHERE id<>%s AND so_hieu=%s AND status IN ('ready','publishing') "
                        "LIMIT 1", (iid, so_hieu))
            return cur.fetchone() is not None
    finally:
        conn.close()


def _diff(parsed: dict) -> dict:
    try:
        conn = store.connect()
        try:
            return store.compute_diff(conn, parsed)
        finally:
            conn.close()
    except Exception as e:
        return {"error": f"Không so sánh được với dữ liệu hiện có: {e}", "items": [], "counts": {}}


# ── Sửa trước khi duyệt ──────────────────────────────────────────────────────
EDITABLE = ("so_hieu", "loai", "trich_yeu", "co_quan", "ngay_ban_hanh", "hieu_luc_tu", "is_vbhn", "goc_so_hieu")


def update_item(iid: str, patch: dict) -> dict:
    item = get_row(iid)
    if not item or item.get("kind") != "vanban":
        raise KeyError(iid)
    if item["status"] not in ("ready", "error"):
        raise ValueError("Chỉ sửa được mục đang chờ duyệt")
    doc = item.get("doc") or {}
    if not doc:
        raise ValueError("Mục chưa xử lý xong")
    for k in EDITABLE:
        if k in patch:
            doc[k] = bool(patch[k]) if k == "is_vbhn" else str(patch[k] or "").strip()
    if "relations" in patch:
        want = {(r["loai"], r["so_hieu"]): bool(r.get("xac_nhan")) for r in patch["relations"]}
        for r in doc.get("relations", []):
            r["xac_nhan"] = want.get((r["loai"], r["so_hieu"]), r.get("xac_nhan", False))
    for r in patch.get("new_relations") or []:
        if r.get("so_hieu") and r.get("loai") in ("sua_doi", "bai_bo", "het_hieu_luc", "hop_nhat"):
            doc.setdefault("relations", []).append(
                {"loai": r["loai"], "so_hieu": r["so_hieu"].strip(), "trich": "Thêm tay", "xac_nhan": True})
    loai, so = doc.get("loai") or "", doc.get("so_hieu") or ""
    doc["ten_day_du"] = f"{loai} số {so} {doc.get('trich_yeu') or ''}".strip() if so else doc.get("ten_day_du")
    doc["errors"] = [e for e in doc.get("errors", [])
                     if not (e.startswith("Không tìm thấy số hiệu") and doc.get("so_hieu"))]
    fields: dict[str, Any] = {"doc": doc, "diff": _diff(doc), "status": "error" if doc["errors"] else "ready",
                              "error": "; ".join(doc["errors"]) or None}
    for k in ("demuc_id", "chude_id"):
        if k in patch:
            fields[k] = patch[k] or ""
    _set(iid, **fields)
    return get_item(iid)


def reject(iid: str) -> None:
    if not _set(iid, status="rejected", where_status="ready") and not _set(iid, status="rejected", where_status="error"):
        raise ValueError("Chỉ từ chối được mục đang chờ duyệt hoặc bị lỗi")


def retry(iid: str) -> None:
    item = get_row(iid, "id, kind, status")
    if not item:
        raise KeyError(iid)
    if item["status"] == "publish_error":
        _set(iid, status="publishing")
        enqueue("publish_phapdien" if item["kind"] == "phapdien" else "publish", iid)
    elif item["status"] in ("error", "rejected", "ready") and item["kind"] == "vanban":
        _set(iid, status="queued")
        enqueue("process", iid)
    else:
        raise ValueError("Không thể xử lý lại mục này lúc này")


# ── Duyệt → ghi vào kho ──────────────────────────────────────────────────────
def approve(iid: str, actor: str) -> None:
    if _set(iid, where_status="ready", status="publishing", approved_by=actor) != 1:
        raise ValueError("Chỉ duyệt được mục ở trạng thái 'chờ duyệt' (không có lỗi)")
    item = get_row(iid, "kind")
    enqueue("publish_phapdien" if item and item["kind"] == "phapdien" else "publish", iid)


def publish(iid: str) -> None:
    item = get_row(iid)
    if not item or item["status"] != "publishing":
        return
    item["_id"] = iid
    try:
        # Tính lại diff ngay trước khi ghi (dữ liệu có thể đã đổi từ lúc xử lý)
        item["diff"] = _diff(item["doc"])
        if item["diff"].get("error"):
            raise RuntimeError(item["diff"]["error"])
        report = store.publish_document(item)
    except Exception as e:
        traceback.print_exc()
        _set(iid, status="publish_error", error=f"Ghi MySQL lỗi (đã rollback): {e}")
        return
    try:
        report.update(store.reindex_mapcs(report["changed_mapcs"]))
    except Exception as e:
        report["vector_error"] = str(e)
    store.rebuild_bm25_async()
    report["changed"] = len(report.pop("changed_mapcs", []))
    if report.get("vector_error"):
        _set(iid, status="publish_error", publish_report=report, diff=item["diff"],
             error=f"Đã ghi MySQL nhưng embed Milvus lỗi: {report['vector_error']} — bấm 'Thử lại'.")
    else:
        _set(iid, status="published", publish_report=report, diff=item["diff"], error=None, published_at=_now())


# ── Đọc ──────────────────────────────────────────────────────────────────────
SUMMARY_COLS = ("id, kind, status, so_hieu, ten, so_dieu, source, error, created_by, approved_by, "
                "created_at, updated_at, published_at, phapdien_summary, "
                "JSON_LENGTH(JSON_EXTRACT(doc, '$.warnings')) AS warnings, "
                "JSON_EXTRACT(diff, '$.counts') AS counts, JSON_EXTRACT(doc, '$.is_vbhn') AS is_vbhn")


def list_items(status: str = "", limit: int = 100) -> list[dict]:
    where, args = "", []
    if status == "active":
        where = "WHERE status NOT IN ('published','rejected')"
    elif status:
        where, args = "WHERE status=%s", [status]
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {SUMMARY_COLS} FROM law_staging {where} ORDER BY created_at DESC LIMIT %s",
                        [*args, limit])
            rows = cur.fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        r = _row_out(r)
        if isinstance(r.get("counts"), str):
            r["counts"] = json.loads(r["counts"])
        r["is_vbhn"] = r.get("is_vbhn") in ("true", 1, True)
        out.append(r)
    return out


def get_item(iid: str) -> dict:
    r = get_row(iid)
    if not r:
        raise KeyError(iid)
    return r
