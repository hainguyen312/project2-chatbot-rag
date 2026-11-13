"""
Cập nhật dữ liệu pháp điển từ một bản xuất mới (khi tải được pháp điển mới).

Định dạng nhận (JSON hoặc JSONL, cùng cột với bảng hiện tại):
  {"dieu":   [{mapc, ten, noidung, vbqppl?, vbqppl_link?, chuong_id?, demuc_id?, chude_id?, chimuc?, stt?}],
   "chuong": [{mapc, ten, demuc_id?, chimuc?, stt?}],      # tuỳ chọn
   "demuc":  [{id, ten, chude_id?, stt?}],                  # tuỳ chọn
   "chude":  [{id, ten, stt?}]}                             # tuỳ chọn
  hoặc chỉ một mảng các điều / mỗi dòng một điều (JSONL).

So sánh theo mapc:
  mapc mới → thêm | nội dung đổi → lưu bản cũ vào dieu_lichsu rồi cập nhật | giống → bỏ qua
  "bản đầy đủ": mapc không còn trong bản mới → đánh dấu hết hiệu lực (không xoá)
  điều trùng với điều đã thêm từ văn bản (VB:…) → dùng mapc pháp điển, mã VB:… thành alias
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from services.lawdata import staging, store

SAMPLE = 50


def _h(s: str) -> str:
    return hashlib.sha1(store.norm_text(s).encode()).hexdigest()


def load_dump(data: bytes) -> dict[str, list[dict]]:
    text = data.decode("utf-8-sig")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    if isinstance(obj, list):
        obj = {"dieu": obj}
    dieu = obj.get("dieu") or []
    bad = [i for i, d in enumerate(dieu) if not (d.get("mapc") and d.get("ten") and d.get("noidung") is not None)]
    if not dieu:
        raise ValueError("File không có điều nào (cần khoá 'dieu' hoặc một mảng điều)")
    if bad:
        raise ValueError(f"{len(bad)} điều thiếu mapc/ten/noidung (vd. dòng {bad[:5]})")
    return {k: obj.get(k) or [] for k in ("dieu", "chuong", "demuc", "chude")}


def compute(dump: dict, full: bool) -> dict:
    conn = store.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT mapc, ten, noidung, vbqppl FROM pddieu")
            have = {r["mapc"]: r for r in cur.fetchall()}
    finally:
        conn.close()
    vb_by_pair = {store.source_pair(r["vbqppl"]): m for m, r in have.items() if m.startswith("VB:")}
    new, upd, same, alias = [], [], 0, []
    seen = set()
    for d in dump["dieu"]:
        m = d["mapc"]
        seen.add(m)
        old = have.get(m)
        if not old:
            new.append(m)
            pair = store.source_pair(d.get("vbqppl") or "")
            if pair and pair in vb_by_pair:
                alias.append({"alias": vb_by_pair[pair], "mapc": m})
        elif _h(old["noidung"]) != _h(d["noidung"]) or (d.get("vbqppl") and d["vbqppl"] != old["vbqppl"]):
            upd.append(m)
        else:
            same += 1
    missing = [m for m in have if full and m not in seen and not m.startswith("VB:")]
    ten = {d["mapc"]: d["ten"] for d in dump["dieu"]}
    return {
        "full": full,
        "counts": {"new": len(new), "update": len(upd), "same": same, "missing": len(missing), "alias": len(alias)},
        "sample": {
            "new": [{"mapc": m, "ten": ten[m]} for m in new[:SAMPLE]],
            "update": [{"mapc": m, "ten": ten[m]} for m in upd[:SAMPLE]],
            "missing": [{"mapc": m, "ten": have[m]["ten"]} for m in missing[:SAMPLE]],
            "alias": alias[:SAMPLE],
        },
        "_lists": {"new": new, "update": upd, "missing": missing, "alias": alias},
    }


def create(data: bytes, filename: str, full: bool, actor: str) -> dict:
    dump = load_dump(data)  # kiểm tra định dạng trước khi nhận
    iid = uuid.uuid4().hex
    path = staging.DATA_DIR / "phapdien" / f"{iid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    res = compute(dump, full)
    lists = res.pop("_lists")
    (path.parent / f"{iid}.lists.json").write_text(json.dumps(lists, ensure_ascii=False), encoding="utf-8")
    staging.insert_row(iid, "phapdien", "ready", {"type": "file", "name": filename, "nguon": "phapdien"}, actor,
                       phapdien_summary=res, ten=f"Cập nhật pháp điển: {filename}")
    return {"id": iid, **res}


def apply(iid: str) -> None:
    item = staging.get_row(iid, "id, status")
    if not item or item["status"] != "publishing":
        return
    base = staging.DATA_DIR / "phapdien"
    try:
        dump = load_dump((base / f"{iid}.json").read_bytes())
        lists = json.loads((base / f"{iid}.lists.json").read_text(encoding="utf-8"))
        changed = _apply_mysql(iid, dump, lists)
    except Exception as e:
        staging._set(iid, status="publish_error", error=f"Ghi MySQL lỗi (đã rollback): {e}")
        return
    report: dict[str, Any] = {"changed": len(changed)}
    try:
        report.update(store.reindex_mapcs(changed, embed_title_only=True))
        if lists["alias"]:
            from retrieve.two_stage_search import collection

            collection.delete(f'metadata["mapc"] in {json.dumps([a["alias"] for a in lists["alias"]], ensure_ascii=False)}')
    except Exception as e:
        report["vector_error"] = str(e)
    store.rebuild_bm25_async()
    if report.get("vector_error"):
        staging._set(iid, status="publish_error", publish_report=report,
                     error=f"Đã ghi MySQL nhưng embed Milvus lỗi: {report['vector_error']}")
    else:
        staging._set(iid, status="published", publish_report=report, error=None)


def _apply_mysql(iid: str, dump: dict, lists: dict) -> list[str]:
    now = datetime.now()
    by_mapc = {d["mapc"]: d for d in dump["dieu"]}
    changed: list[str] = []
    conn = store.connect()
    try:
        with conn.cursor() as cur:
            for c in dump["chude"]:
                cur.execute("INSERT INTO pdchude (id, ten, stt) VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE ten=VALUES(ten)",
                            (c["id"], c["ten"], c.get("stt", 0)))
            for c in dump["demuc"]:
                cur.execute("INSERT INTO pddemuc (id, ten, stt, chude_id) VALUES (%s,%s,%s,%s) "
                            "ON DUPLICATE KEY UPDATE ten=VALUES(ten)", (c["id"], c["ten"], c.get("stt", 0), c.get("chude_id", "")))
            for c in dump["chuong"]:
                cur.execute("INSERT INTO pdchuong (mapc, ten, demuc_id, chimuc, stt) VALUES (%s,%s,%s,%s,%s) "
                            "ON DUPLICATE KEY UPDATE ten=VALUES(ten)",
                            (c["mapc"], c["ten"], c.get("demuc_id", ""), c.get("chimuc", ""), c.get("stt", 0)))
            for m in lists["update"]:
                cur.execute("INSERT INTO dieu_lichsu (mapc, ten, noidung, vbqppl, vbqppl_link, ly_do, staging_id, thay_doi_luc) "
                            "SELECT mapc, ten, noidung, vbqppl, vbqppl_link, 'phapdien', %s, %s FROM pddieu WHERE mapc=%s",
                            (iid, now, m))
            for m in lists["new"] + lists["update"]:
                d = by_mapc[m]
                cur.execute(
                    "INSERT INTO pddieu (mapc, ten, demuc_id, chuong_id, chude_id, noidung, chimuc, vbqppl, vbqppl_link, stt) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE ten=VALUES(ten), "
                    "noidung=VALUES(noidung), vbqppl=VALUES(vbqppl), vbqppl_link=VALUES(vbqppl_link), "
                    "chuong_id=VALUES(chuong_id), demuc_id=VALUES(demuc_id), chude_id=VALUES(chude_id)",
                    (m, d["ten"], d.get("demuc_id", ""), d.get("chuong_id", ""), d.get("chude_id", ""), d["noidung"],
                     int(d.get("chimuc") or 0), d.get("vbqppl", ""), d.get("vbqppl_link"), int(d.get("stt") or 0)),
                )
                changed.append(m)
            for m in lists["missing"]:
                cur.execute("INSERT INTO dieu_hieuluc (mapc, trang_thai, nguon, staging_id, cap_nhat_luc) "
                            "VALUES (%s,'het_hieu_luc','phapdien',%s,%s) ON DUPLICATE KEY UPDATE "
                            "trang_thai='het_hieu_luc', nguon='phapdien', staging_id=VALUES(staging_id), "
                            "cap_nhat_luc=VALUES(cap_nhat_luc)", (m, iid, now))
            for a in lists["alias"]:
                # Điều đã thêm từ văn bản nay có trong pháp điển → giữ lịch sử, chuyển sang mapc pháp điển
                cur.execute("INSERT INTO dieu_lichsu (mapc, ten, noidung, vbqppl, vbqppl_link, ly_do, staging_id, thay_doi_luc) "
                            "SELECT mapc, ten, noidung, vbqppl, vbqppl_link, 'thay_bang_phapdien', %s, %s FROM pddieu "
                            "WHERE mapc=%s", (iid, now, a["alias"]))
                cur.execute("DELETE FROM pddieu WHERE mapc=%s", (a["alias"],))
                cur.execute("INSERT INTO mapc_alias (alias, mapc, tao_luc) VALUES (%s,%s,%s) "
                            "ON DUPLICATE KEY UPDATE mapc=VALUES(mapc)", (a["alias"], a["mapc"], now))
                cur.execute("UPDATE dieu_hieuluc SET mapc=%s WHERE mapc=%s", (a["mapc"], a["alias"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return changed
