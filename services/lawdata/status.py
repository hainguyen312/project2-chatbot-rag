"""
Gắn tình trạng hiệu lực vào kết quả tra cứu (dùng bảng phụ của công cụ dữ liệu).

- Văn bản đã hết hiệu lực → hạ điểm và ghi chú rõ văn bản thay thế/bãi bỏ.
- Văn bản đã bị sửa đổi / có văn bản hợp nhất → ghi chú để agent nói rõ cho người dùng.
Văn bản chưa có trong sổ cái → để nguyên như trước (không đoán).
Mọi lỗi (chưa có bảng phụ, MySQL lỗi) → trả nguyên kết quả.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from services.lawdata.store import connect, source_pair

HET_HIEU_LUC_PENALTY = 0.4
_TEN_TT = {
    "het_hieu_luc": "đã hết hiệu lực",
    "het_hieu_luc_mot_phan": "đã hết hiệu lực một phần",
    "chua_hieu_luc": "chưa có hiệu lực",
}


def _fmt(d: Any) -> str:
    return d.strftime("%d/%m/%Y") if isinstance(d, date) else ""


def status_notes(hits: list[dict]) -> dict[str, dict]:
    """mapc → {trang_thai, ghi_chu}."""
    pairs = {h.get("mapc"): source_pair(h.get("vbqppl") or "") for h in hits if h.get("mapc")}
    so_hieus = sorted({p[1] for p in pairs.values() if p})
    mapcs = [m for m in pairs if m]
    if not so_hieus and not mapcs:
        return {}
    conn = connect()
    try:
        with conn.cursor() as cur:
            vb, rel, dieu = {}, {}, {}
            if so_hieus:
                ph = ",".join(["%s"] * len(so_hieus))
                cur.execute(f"SELECT so_hieu, trang_thai, hieu_luc_tu, hieu_luc_den, thay_the_boi FROM vanban "
                            f"WHERE so_hieu IN ({ph})", so_hieus)
                vb = {r["so_hieu"]: r for r in cur.fetchall()}
                cur.execute(f"SELECT so_hieu, loai, doi_tuong FROM vanban_quanhe WHERE doi_tuong IN ({ph}) "
                            f"AND loai IN ('sua_doi','hop_nhat')", so_hieus)
                for r in cur.fetchall():
                    rel.setdefault(r["doi_tuong"], []).append(r)
            if mapcs:
                ph = ",".join(["%s"] * len(mapcs))
                cur.execute(f"SELECT mapc, trang_thai, hieu_luc_tu, hieu_luc_den FROM dieu_hieuluc "
                            f"WHERE mapc IN ({ph})", mapcs)
                dieu = {r["mapc"]: r for r in cur.fetchall()}
    finally:
        conn.close()

    out: dict[str, dict] = {}
    for mapc, pair in pairs.items():
        so = pair[1] if pair else ""
        v, d = vb.get(so), dieu.get(mapc)
        trang_thai, notes = "", []
        src = d if d and d["trang_thai"] != "con_hieu_luc" else v
        if src and src["trang_thai"] in _TEN_TT:
            trang_thai = src["trang_thai"]
            if trang_thai == "chua_hieu_luc":
                notes.append(f"Văn bản {so} chưa có hiệu lực (có hiệu lực từ {_fmt(src.get('hieu_luc_tu'))}).")
            else:
                s = f"Văn bản {so} {_TEN_TT[trang_thai]}"
                if src.get("hieu_luc_den"):
                    s += f" từ {_fmt(src['hieu_luc_den'])}"
                if v and v.get("thay_the_boi"):
                    s += f", bị bãi bỏ/thay thế bởi {v['thay_the_boi']}"
                notes.append(s + ".")
        amend = sorted({r["so_hieu"] for r in rel.get(so, []) if r["loai"] == "sua_doi"})
        if amend:
            notes.append(f"Văn bản {so} đã được sửa đổi, bổ sung bởi {', '.join(amend)} — kiểm tra nội dung mới.")
        vbhn = sorted({r["so_hieu"] for r in rel.get(so, []) if r["loai"] == "hop_nhat"})
        if vbhn:
            notes.append(f"Có văn bản hợp nhất: {', '.join(vbhn)}.")
        if notes:
            out[mapc] = {"trang_thai": trang_thai or "con_hieu_luc", "ghi_chu": " ".join(notes)}
    return out


def _vn_date(iso: str | None) -> str:
    return "/".join(reversed(iso.split("-"))) if iso else ""


def vbqppl_notes(hits: list[dict]) -> dict[str, dict]:
    """
    Tình trạng theo ghi chú của chính Pháp Điển trong vbqppl ("… bị bãi bỏ bởi Điều 2 Nghị định số X",
    "… có nội dung được sửa đổi, bổ sung bởi …") — có sẵn cho cả 66k điều gốc, không cần sổ cái admin.
    """
    from retrieve.legal_graph import parse_vbqppl

    out: dict[str, dict] = {}
    for h in hits:
        p = parse_vbqppl(h.get("vbqppl"))
        if not h.get("mapc") or not p or not p["hanh_vi"] or not p["sua_doi_boi"]:
            continue
        refs = "; ".join(
            f"Điều {', '.join(a['dieu'])} {a['loai']} số {a['so_hieu']}"
            + (f" (hiệu lực từ {_vn_date(a['hieu_luc_tu'])})" if a.get("hieu_luc_tu") else "")
            for a in p["sua_doi_boi"]
        )
        if "bai_bo" in p["hanh_vi"]:
            out[h["mapc"]] = {"trang_thai": "het_hieu_luc", "ghi_chu": f"Điều này đã bị bãi bỏ bởi {refs}."}
            continue
        acts = [t for k, t in (("sua_doi", "sửa đổi"), ("bo_sung", "bổ sung"), ("bai_bo_mot_phan", "bãi bỏ một phần"))
                if k in p["hanh_vi"]]
        out[h["mapc"]] = {
            "trang_thai": "het_hieu_luc_mot_phan" if "bai_bo_mot_phan" in p["hanh_vi"] else "con_hieu_luc",
            "ghi_chu": f"Điều này có nội dung được {', '.join(acts)} bởi {refs}; nội dung Pháp Điển đã cập nhật "
                       "đến lần sửa đổi này — văn bản sửa đổi mới hơn (nếu có) cần kiểm tra thêm.",
        }
    return out


def annotate_hits(hits: list[dict]) -> list[dict]:
    """Thêm hit['hieu_luc'] (sổ cái admin ưu tiên, sau đó ghi chú vbqppl của Pháp Điển);
    hạ điểm điều đã hết hiệu lực / bị bãi bỏ rồi sắp xếp lại."""
    if not hits:
        return hits
    pd_hits = [h for h in hits if h.get("source", "phapdien") != "web"]
    try:
        notes = status_notes(pd_hits)
    except Exception as e:
        print(f"[Hiệu lực] Bỏ qua sổ cái hiệu lực: {e}")
        notes = {}
    notes = {**vbqppl_notes(pd_hits), **notes}
    if not notes:
        return hits
    for h in hits:
        n = notes.get(h.get("mapc"))
        if n:
            h["hieu_luc"] = n
            if n["trang_thai"] == "het_hieu_luc":
                h["score"] = float(h.get("score", 0.0)) * HET_HIEU_LUC_PENALTY
    return sorted(hits, key=lambda h: -float(h.get("score", 0.0)))


def resolve_aliases(mapcs: list[str]) -> list[str]:
    """Mã tạm VB:… đã được thay bằng mapc pháp điển → trả mapc mới (chip ở chat cũ vẫn mở được)."""
    vb = [m for m in mapcs if m.startswith("VB:")]
    if not vb:
        return mapcs
    try:
        conn = connect()
        try:
            with conn.cursor() as cur:
                ph = ",".join(["%s"] * len(vb))
                cur.execute(f"SELECT alias, mapc FROM mapc_alias WHERE alias IN ({ph})", vb)
                alias = {r["alias"]: r["mapc"] for r in cur.fetchall()}
        finally:
            conn.close()
    except Exception:
        return mapcs
    return [alias.get(m, m) for m in mapcs]
