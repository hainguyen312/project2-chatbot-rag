"""
Ghi dữ liệu vào kho tra cứu — giữ nguyên bảng pd* và chỉ mục cũ, chỉ THÊM bảng phụ.

Bảng phụ (tạo nếu chưa có):
  vanban           sổ cái văn bản: số hiệu, loại, tình trạng hiệu lực, nguồn
  vanban_quanhe    sửa đổi / bãi bỏ / hết hiệu lực / hợp nhất giữa các văn bản
  vanban_cautruc   cây cấu trúc theo đúng văn bản (Phần/Chương/Mục/Điều)
  dieu_hieuluc     hiệu lực + nguồn của từng điều (khoá mapc)
  dieu_lichsu      nội dung cũ trước mỗi lần cập nhật (không mất dữ liệu)
  mapc_alias       mã tạm VB:… → mapc pháp điển khi pháp điển cập nhật trùng điều

Điều thêm từ văn bản dùng mapc "VB:<số hiệu>:D<số điều>".
Bảng pd* có khoá ngoại + NOT NULL nên mỗi văn bản có một dòng đề mục đại diện
"VB:<số hiệu>" (tên = tên văn bản) thuộc chủ đề chung "VB"; chương lấy theo chính văn bản.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime
from typing import Any, Iterable, Optional

import pymysql

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS vanban (
        so_hieu VARCHAR(80) PRIMARY KEY,
        loai VARCHAR(40), trich_yeu TEXT, co_quan VARCHAR(255),
        ngay_ban_hanh DATE NULL, hieu_luc_tu DATE NULL, hieu_luc_den DATE NULL,
        trang_thai VARCHAR(32) NOT NULL DEFAULT 'con_hieu_luc',
        thay_the_boi VARCHAR(80) NULL, is_vbhn TINYINT NOT NULL DEFAULT 0, goc_so_hieu VARCHAR(80) NULL,
        nguon VARCHAR(32), nguon_url TEXT, staging_id VARCHAR(40), cap_nhat_luc DATETIME
    ) CHARACTER SET utf8mb4""",
    """CREATE TABLE IF NOT EXISTS vanban_quanhe (
        id INT AUTO_INCREMENT PRIMARY KEY,
        so_hieu VARCHAR(80) NOT NULL, loai VARCHAR(20) NOT NULL, doi_tuong VARCHAR(80) NOT NULL,
        trich TEXT, staging_id VARCHAR(40), tao_luc DATETIME,
        UNIQUE KEY uq_qh (so_hieu, loai, doi_tuong), KEY ix_dt (doi_tuong)
    ) CHARACTER SET utf8mb4""",
    """CREATE TABLE IF NOT EXISTS vanban_cautruc (
        id INT AUTO_INCREMENT PRIMARY KEY,
        so_hieu VARCHAR(80) NOT NULL, cap VARCHAR(10) NOT NULL, nhan VARCHAR(255), ten TEXT,
        cha VARCHAR(255), thu_tu INT, mapc VARCHAR(128) NULL, KEY ix_sh (so_hieu)
    ) CHARACTER SET utf8mb4""",
    """CREATE TABLE IF NOT EXISTS dieu_hieuluc (
        mapc VARCHAR(128) PRIMARY KEY,
        so_hieu VARCHAR(80), so_dieu VARCHAR(10),
        trang_thai VARCHAR(32) NOT NULL DEFAULT 'con_hieu_luc',
        hieu_luc_tu DATE NULL, hieu_luc_den DATE NULL, nguon VARCHAR(32), staging_id VARCHAR(40),
        cap_nhat_luc DATETIME, KEY ix_sh (so_hieu)
    ) CHARACTER SET utf8mb4""",
    """CREATE TABLE IF NOT EXISTS dieu_lichsu (
        id INT AUTO_INCREMENT PRIMARY KEY,
        mapc VARCHAR(128) NOT NULL, ten TEXT, noidung MEDIUMTEXT, vbqppl TEXT, vbqppl_link TEXT,
        ly_do VARCHAR(64), staging_id VARCHAR(40), thay_doi_luc DATETIME, KEY ix_mapc (mapc)
    ) CHARACTER SET utf8mb4""",
    """CREATE TABLE IF NOT EXISTS mapc_alias (
        alias VARCHAR(128) PRIMARY KEY, mapc VARCHAR(128) NOT NULL, tao_luc DATETIME
    ) CHARACTER SET utf8mb4""",
]

VB_CHUDE = "VB"
VB_CHUDE_TEN = "Văn bản bổ sung ngoài pháp điển"
TRANG_THAI = ("con_hieu_luc", "chua_hieu_luc", "het_hieu_luc_mot_phan", "het_hieu_luc")
_SOURCE_PAIR = re.compile(r"Điều\s+(\d+[a-zđ]?)\s+[^,()]*?số\s+([^\s,()]+)")
_schema_ready = False


def mysql_cfg() -> dict:
    return {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", "123456789"),
        "database": os.getenv("MYSQL_DATABASE", "law"),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
    }


def connect():
    global _schema_ready
    conn = pymysql.connect(**mysql_cfg())
    if not _schema_ready:
        with conn.cursor() as cur:
            for sql in SCHEMA:
                cur.execute(sql)
        conn.commit()
        _schema_ready = True
    return conn


def _d(s: str | None):
    return s or None


def _now() -> datetime:
    return datetime.now()


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def source_pair(vbqppl: str) -> tuple[str, str] | None:
    """'(Điều 21 Luật số 26/2023/QH15, …)' → ('21', '26/2023/QH15')."""
    m = _SOURCE_PAIR.search(vbqppl or "")
    return (m.group(1), m.group(2)) if m else None


# ── Văn bản → điều cần ghi ───────────────────────────────────────────────────
def target_so_hieu(doc: dict) -> str:
    """Văn bản hợp nhất cập nhật điều của VĂN BẢN GỐC; còn lại là chính nó."""
    return (doc.get("goc_so_hieu") if doc.get("is_vbhn") else "") or doc.get("so_hieu", "")


def vb_mapc(target: str, so_dieu: str) -> str:
    return f"VB:{target}:D{so_dieu}"


def build_vbqppl(doc: dict, so_dieu: str) -> str:
    target = target_so_hieu(doc)
    s = f"(Điều {so_dieu} {doc.get('loai') or 'Văn bản'} số {target}"
    if doc.get("is_vbhn") and doc.get("so_hieu"):
        s += f" theo văn bản hợp nhất số {doc['so_hieu']}"
    if hl := doc.get("hieu_luc_tu"):
        y, m, d = hl.split("-")
        s += f", có hiệu lực thi hành kể từ ngày {d}/{m}/{y}"
    return s + ")"


def existing_articles(conn, target: str) -> dict[str, dict]:
    """Điều của văn bản `target` đang có trong pddieu (pháp điển hoặc đã thêm trước đó)."""
    if not target:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT mapc, ten, noidung, vbqppl, vbqppl_link FROM pddieu WHERE vbqppl LIKE %s",
                    (f"%{target}%",))
        rows = cur.fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        pair = source_pair(r["vbqppl"])
        if pair and pair[1] == target and pair[0] not in out:
            out[pair[0]] = r
    return out


def compute_diff(conn, doc: dict) -> dict:
    target = target_so_hieu(doc)
    have = existing_articles(conn, target)
    items, counts = [], {"new": 0, "update": 0, "same": 0}
    for a in doc.get("articles", []):
        old = have.get(a["so"])
        if not old:
            action = "new"
        elif norm_text(old["noidung"]) == norm_text(a["noidung"]):
            action = "same"
        else:
            action = "update"
        counts[action] += 1
        items.append({
            "so": a["so"], "action": action,
            "mapc": old["mapc"] if old else vb_mapc(target, a["so"]),
            "old_ten": old["ten"] if old else None,
            "old_noidung": old["noidung"] if old and action == "update" else None,
        })
    in_doc = {a["so"] for a in doc.get("articles", [])}
    not_in_doc = sorted(set(have) - in_doc, key=lambda x: (len(x), x))
    return {"target": target, "existing": len(have), "counts": counts, "items": items,
            "not_in_doc": not_in_doc}


# ── Ghi khi duyệt ────────────────────────────────────────────────────────────
def _status_for(doc: dict) -> str:
    hl = doc.get("hieu_luc_tu")
    return "chua_hieu_luc" if hl and hl > date.today().isoformat() else "con_hieu_luc"


def _upsert_vanban(cur, so_hieu: str, fields: dict) -> None:
    fields = {k: v for k, v in fields.items() if v is not None}
    cols = ["so_hieu", *fields]
    vals = [so_hieu, *fields.values()]
    upd = ", ".join(f"{c}=VALUES({c})" for c in fields)
    cur.execute(
        f"INSERT INTO vanban ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
        + (f" ON DUPLICATE KEY UPDATE {upd}" if upd else " ON DUPLICATE KEY UPDATE so_hieu=so_hieu"),
        vals,
    )


def publish_document(staging: dict) -> dict:
    """Ghi một văn bản đã duyệt vào MySQL (một transaction). Trả danh sách mapc cần embed lại."""
    doc, diff = staging["doc"], staging["diff"]
    sid, src = staging["_id"], staging.get("source", {})
    target = target_so_hieu(doc)
    nguon = src.get("nguon") or src.get("type") or "upload"
    link = src.get("url") or None
    now = _now()
    changed: list[str] = []
    conn = connect()
    try:
        with conn.cursor() as cur:
            # Bảng pd* có khoá ngoại + NOT NULL cho chủ đề/đề mục/chương → không để trống được.
            # Giữ nguyên cấu trúc bảng: mỗi văn bản có một dòng đề mục đại diện (tên = tên văn bản)
            # dưới chủ đề chung VB_CHUDE; admin chọn đề mục pháp điển thật thì dùng đề mục đó.
            demuc_id, chude_id = staging.get("demuc_id") or "", staging.get("chude_id") or ""
            has_new = any(it["action"] == "new" for it in diff["items"])
            if not has_new:
                pass  # chỉ cập nhật điều đã có → không cần đề mục/chương mới
            elif not demuc_id:
                cur.execute("INSERT IGNORE INTO pdchude (id, ten, stt) VALUES (%s,%s,%s)", (VB_CHUDE, VB_CHUDE_TEN, 9999))
                demuc_id, chude_id = f"VB:{target}", VB_CHUDE
                cur.execute("INSERT INTO pddemuc (id, ten, stt, chude_id) VALUES (%s,%s,%s,%s) "
                            "ON DUPLICATE KEY UPDATE ten=VALUES(ten)",
                            (demuc_id, f"{doc.get('loai') or ''} {doc.get('trich_yeu') or target}".strip(), 0, VB_CHUDE))
            elif not chude_id:
                cur.execute("SELECT chude_id FROM pddemuc WHERE id=%s", (demuc_id,))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Đề mục {demuc_id} không tồn tại")
                chude_id = row["chude_id"]
            # Chương theo chính văn bản; điều không thuộc chương nào → dòng chương rỗng C0
            chuong_ids: dict[str, str] = {}
            for a in doc["articles"] if has_new else []:
                ch = a.get("chuong") or ""
                if ch not in chuong_ids:
                    label = ch.split(".")[0].replace("Chương ", "").strip() if ch else "0"
                    cid = f"VB:{target}:C{label}"
                    chuong_ids[ch] = cid
                    cur.execute(
                        "INSERT INTO pdchuong (mapc, ten, demuc_id, chimuc, stt) VALUES (%s,%s,%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE ten=VALUES(ten), demuc_id=VALUES(demuc_id)",
                        (cid, ch, demuc_id, label if ch else "", len(chuong_ids)),
                    )
            by_so = {it["so"]: it for it in diff["items"]}
            for i, a in enumerate(doc["articles"], start=1):
                it = by_so.get(a["so"]) or {"action": "new", "mapc": vb_mapc(target, a["so"])}
                mapc, vbqppl = it["mapc"], build_vbqppl(doc, a["so"])
                if it["action"] == "new":
                    cur.execute(
                        "INSERT INTO pddieu (mapc, ten, demuc_id, chuong_id, chude_id, noidung, chimuc, vbqppl, "
                        "vbqppl_link, stt) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE ten=VALUES(ten), noidung=VALUES(noidung), vbqppl=VALUES(vbqppl)",
                        (mapc, f"Điều {a['so']}. {a['ten']}".strip(), demuc_id, chuong_ids[a.get("chuong") or ""],
                         chude_id, a["noidung"], i, vbqppl, link, i),
                    )
                    changed.append(mapc)
                elif it["action"] == "update":
                    cur.execute("SELECT ten, noidung, vbqppl, vbqppl_link FROM pddieu WHERE mapc=%s", (mapc,))
                    old = cur.fetchone()
                    if old:
                        cur.execute(
                            "INSERT INTO dieu_lichsu (mapc, ten, noidung, vbqppl, vbqppl_link, ly_do, staging_id, "
                            "thay_doi_luc) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                            (mapc, old["ten"], old["noidung"], old["vbqppl"], old["vbqppl_link"],
                             "hop_nhat" if doc.get("is_vbhn") else "cap_nhat", sid, now),
                        )
                    # Giữ tên điều cũ (tên pháp điển có mã), chỉ thay nội dung + nguồn
                    cur.execute("UPDATE pddieu SET noidung=%s, vbqppl=%s, vbqppl_link=COALESCE(%s, vbqppl_link) "
                                "WHERE mapc=%s", (a["noidung"], vbqppl, link, mapc))
                    changed.append(mapc)
                cur.execute(
                    "INSERT INTO dieu_hieuluc (mapc, so_hieu, so_dieu, trang_thai, hieu_luc_tu, nguon, staging_id, "
                    "cap_nhat_luc) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
                    "trang_thai=VALUES(trang_thai), hieu_luc_tu=VALUES(hieu_luc_tu), nguon=VALUES(nguon), "
                    "staging_id=VALUES(staging_id), cap_nhat_luc=VALUES(cap_nhat_luc)",
                    (mapc, target, a["so"], _status_for(doc), _d(doc.get("hieu_luc_tu")), nguon, sid, now),
                )

            # Cây cấu trúc theo văn bản
            cur.execute("DELETE FROM vanban_cautruc WHERE so_hieu=%s", (target,))
            order, seen = 0, set()
            for a in doc["articles"]:
                for cap, nhan, cha in (("phan", a.get("phan"), ""), ("chuong", a.get("chuong"), a.get("phan")),
                                       ("muc", a.get("muc"), a.get("chuong"))):
                    if nhan and (cap, nhan) not in seen:
                        seen.add((cap, nhan)); order += 1
                        cur.execute("INSERT INTO vanban_cautruc (so_hieu, cap, nhan, ten, cha, thu_tu) "
                                    "VALUES (%s,%s,%s,%s,%s,%s)", (target, cap, nhan[:255], nhan, cha or "", order))
                order += 1
                cur.execute("INSERT INTO vanban_cautruc (so_hieu, cap, nhan, ten, cha, thu_tu, mapc) "
                            "VALUES (%s,'dieu',%s,%s,%s,%s,%s)",
                            (target, f"Điều {a['so']}", a["ten"], a.get("muc") or a.get("chuong") or a.get("phan") or "",
                             order, by_so.get(a["so"], {}).get("mapc") or vb_mapc(target, a["so"])))

            base = {"loai": doc.get("loai"), "trich_yeu": doc.get("trich_yeu"), "co_quan": doc.get("co_quan"),
                    "nguon": nguon, "nguon_url": link, "staging_id": sid, "cap_nhat_luc": now}
            _upsert_vanban(cur, doc["so_hieu"], {
                **base, "ngay_ban_hanh": _d(doc.get("ngay_ban_hanh")), "hieu_luc_tu": _d(doc.get("hieu_luc_tu")),
                "trang_thai": _status_for(doc), "is_vbhn": 1 if doc.get("is_vbhn") else 0,
                "goc_so_hieu": doc.get("goc_so_hieu") or None,
            })
            if doc.get("is_vbhn") and target and target != doc["so_hieu"]:
                _upsert_vanban(cur, target, {"loai": doc.get("loai"), "trich_yeu": doc.get("trich_yeu"),
                                             "cap_nhat_luc": now})

            applied = []
            for r in doc.get("relations", []):
                if not r.get("xac_nhan"):
                    continue
                cur.execute("INSERT IGNORE INTO vanban_quanhe (so_hieu, loai, doi_tuong, trich, staging_id, tao_luc) "
                            "VALUES (%s,%s,%s,%s,%s,%s)", (doc["so_hieu"], r["loai"], r["so_hieu"], r.get("trich"), sid, now))
                if r["loai"] in ("bai_bo", "het_hieu_luc"):
                    _upsert_vanban(cur, r["so_hieu"], {"trang_thai": "het_hieu_luc",
                                                        "hieu_luc_den": _d(doc.get("hieu_luc_tu")),
                                                        "thay_the_boi": doc["so_hieu"], "cap_nhat_luc": now})
                applied.append(f"{r['loai']}:{r['so_hieu']}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"changed_mapcs": changed, "relations_applied": applied, "target": target}


def set_vanban_status(so_hieu: str, trang_thai: str, hieu_luc_den: str = "", thay_the_boi: str = "") -> None:
    if trang_thai not in TRANG_THAI:
        raise ValueError(f"trang_thai phải là một trong {TRANG_THAI}")
    conn = connect()
    try:
        with conn.cursor() as cur:
            _upsert_vanban(cur, so_hieu, {"trang_thai": trang_thai, "hieu_luc_den": _d(hieu_luc_den),
                                          "thay_the_boi": thay_the_boi or None, "cap_nhat_luc": _now()})
        conn.commit()
    finally:
        conn.close()


def list_vanban(q: str = "", limit: int = 100) -> list[dict]:
    conn = connect()
    try:
        with conn.cursor() as cur:
            like = f"%{q}%"
            cur.execute("SELECT so_hieu, loai, trich_yeu, trang_thai, hieu_luc_tu, hieu_luc_den, thay_the_boi, "
                        "is_vbhn, goc_so_hieu, nguon, cap_nhat_luc FROM vanban "
                        "WHERE so_hieu LIKE %s OR trich_yeu LIKE %s ORDER BY cap_nhat_luc DESC LIMIT %s",
                        (like, like, limit))
            rows = cur.fetchall()
    finally:
        conn.close()
    for r in rows:
        for k, v in r.items():
            if isinstance(v, (date, datetime)):
                r[k] = v.isoformat()
    return rows


def list_demuc() -> list[dict]:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT dm.id, dm.ten, dm.chude_id, cd.ten AS chude FROM pddemuc dm "
                        "LEFT JOIN pdchude cd ON cd.id = dm.chude_id WHERE dm.chude_id <> %s ORDER BY cd.stt, dm.stt",
                        (VB_CHUDE,))
            return list(cur.fetchall())
    finally:
        conn.close()


# ── Chỉ mục: Milvus (cùng collection) + BM25 ─────────────────────────────────
def milvus_id(mapc: str) -> int:
    """id ổn định theo mapc, tách khỏi dải id tuần tự của dữ liệu pháp điển ban đầu."""
    return 10**12 + int(hashlib.sha1(mapc.encode()).hexdigest()[:13], 16)


def reindex_mapcs(mapcs: Iterable[str], *, embed_title_only: bool = False) -> dict:
    """Xoá vector cũ theo mapc rồi embed + chèn lại. Embed giống bộ seed: tên điều (+ tên văn bản)."""
    mapcs = list(dict.fromkeys(m for m in mapcs if m))
    if not mapcs:
        return {"embedded": 0}
    from retrieve.two_stage_search import collection  # đã kết nối Milvus
    from services.utils import embed_batch

    if collection is None:
        raise RuntimeError("Milvus collection chưa sẵn sàng")
    conn = connect()
    try:
        with conn.cursor() as cur:
            ph = ",".join(["%s"] * len(mapcs))
            cur.execute(f"""SELECT d.mapc, d.ten, d.noidung, d.vbqppl, ch.ten AS tenchuong, dm.ten AS tendemuc,
                                   cd.ten AS tenchude, vb.loai, vb.trich_yeu
                            FROM pddieu d LEFT JOIN pdchuong ch ON ch.mapc=d.chuong_id
                            LEFT JOIN pddemuc dm ON dm.id=d.demuc_id LEFT JOIN pdchude cd ON cd.id=d.chude_id
                            LEFT JOIN dieu_hieuluc h ON h.mapc=d.mapc LEFT JOIN vanban vb ON vb.so_hieu=h.so_hieu
                            WHERE d.mapc IN ({ph})""", mapcs)
            rows = list(cur.fetchall())
    finally:
        conn.close()
    collection.delete(f'metadata["mapc"] in {json.dumps(mapcs, ensure_ascii=False)}')
    done = 0
    for i in range(0, len(rows), 64):
        batch = rows[i:i + 64]
        texts = []
        for r in batch:
            t = r["ten"] or ""
            # Điều thêm từ văn bản: tên điều như "Phạm vi điều chỉnh" rất chung → kèm tên văn bản
            if not embed_title_only and r["mapc"].startswith("VB:") and r.get("loai"):
                t += f" — {r['loai']} {r.get('trich_yeu') or ''}".rstrip()
            texts.append(t[:2000])
        vecs = embed_batch(texts)
        metas = []
        for r in batch:
            meta = {"mapc": r["mapc"], "tenchude": r["tenchude"], "tendemuc": r["tendemuc"],
                    "tenchuong": r["tenchuong"], "tendieu": r["ten"], "noidung": r["noidung"] or ""}
            while len(json.dumps(meta, ensure_ascii=False).encode()) > 64_000:
                meta["noidung"] = meta["noidung"][:-4000]
            metas.append(meta)
        collection.insert([[milvus_id(r["mapc"]) for r in batch], metas, vecs])
        done += len(batch)
    collection.flush()
    return {"embedded": done}


def rebuild_bm25_async() -> None:
    from retrieve.lexical_bm25 import rebuild_async

    rebuild_async()
