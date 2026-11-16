"""
Đồ thị pháp luật trên Neo4j: phân tích văn bản → node/cạnh, ghi đồ thị (build toàn bộ và đồng bộ từng phần).

Vai trò của Neo4j: tầng CẤU TRÚC + QUAN HỆ giữa các điều luật. MySQL (`pddieu`) vẫn là nguồn chuẩn;
mọi thứ trong đồ thị đều suy ra được từ MySQL nên có thể build lại bất cứ lúc nào.

    (:ChuDe)-[:CO_DE_MUC]->(:DeMuc)-[:CO_CHUONG]->(:Chuong)-[:CO_DIEU]->(:Dieu)
    (:Dieu {mapc, ten, noidung, vbqppl, so_hieu, so_dieu, ngoai_le})
    (:Dieu)-[:CO_KHOAN]->(:Khoan {id, mapc, so, noidung, ngoai_le})        điều có ≥ 2 khoản
    (:Dieu)-[:THUOC]->(:VanBan {so_hieu, loai, hieu_luc_tu, trang_thai})
    (:Dieu)-[:THAM_CHIEU {khoan, diem, pham_vi, ngoai_le}]->(:Dieu)        dẫn chiếu trong nội dung
    (:Dieu)-[:LIEN_QUAN]-(:Dieu)                                           "mục liên quan" của Pháp Điển
    (:Dieu)-[:DUOC_SUA_DOI_BOI {hanh_vi, dieu, hieu_luc_tu}]->(:VanBan)    ghi chú trong vbqppl
    (:VanBan)-[:SUA_DOI_BO_SUNG|BAI_BO|THAY_THE|HOP_NHAT]->(:VanBan)

Phần trên cùng là các hàm thuần (không chạm DB) để test được; phần dưới là các hàm ghi Neo4j.
"""
from __future__ import annotations

import os
import re
from typing import Any, Iterable

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")

BATCH = 500
KHOAN_BATCH = 2000

# ═════════════════════════════════════════════════════════════════════════════
# 1. Phân tích văn bản (hàm thuần)
# ═════════════════════════════════════════════════════════════════════════════
# Số hiệu văn bản: 100/2019/NĐ-CP, 52/2014/QH13, 01/2020/TTLT-VKSNDTC-…-BNN&PTNT-BTC-BTP, 01/VBHN-VPQH
SO_HIEU = r"\d+(?:/\d{4})?/[\wĐđ&\-]*[\wĐđ]"
DOC_TYPES = (r"Bộ luật|Luật|Nghị định|Thông tư liên tịch|Thông tư|Pháp lệnh|Quyết định|Nghị quyết|"
             r"Hiến pháp|Quy chế|Quy định|Điều lệ")

# ── Ngoại lệ / cấm ───────────────────────────────────────────────────────────
# Điều/khoản giới hạn một quy định khác: "điều chính cho phép nhưng điều này cấm" (cấm kết hôn trong phạm vi
# ba đời, không được kỷ luật lao động nữ mang thai…). Bỏ "Trường hợp không…" vì thường chỉ là câu điều kiện.
_EXC_CLAUSE = re.compile(
    r"^(?:\d+\.\s*)?(?:Không được|Nghiêm cấm|Cấm|Không áp dụng|Không xử lý|Không thực hiện|Không cho phép|"
    r"Không chấp nhận|Không cấp|Không giải quyết|Không bắt buộc|Trừ trường hợp|Các trường hợp không)(?![\wĐđ])"
)
_EXC_TITLE = re.compile(
    r"(?i)(không được|nghiêm cấm|bị cấm|(?<![\wĐđ])cấm(?![\wĐđ])|không áp dụng|trường hợp không|hành vi bị|"
    r"không xử lý|không cấp|không chấp nhận|miễn trừ|ngoại lệ)"
)
# Ngữ cảnh dẫn chiếu mang nghĩa ngoại lệ: "… trừ trường hợp quy định tại Điều 36", "không thuộc … khoản 2 Điều 5"
_EXC_CONTEXT = re.compile(
    r"(?i)(trừ\s+(?:các\s+)?trường hợp|trừ\s+(?:các\s+)?hành vi|không được|không áp dụng|không thuộc|"
    r"(?<![\wĐđ])cấm(?![\wĐđ])|ngoại trừ|không bị)"
)


def is_exception_clause(text: str | None) -> bool:
    return bool(_EXC_CLAUSE.match((text or "").strip()))


def is_exception_title(ten: str | None) -> bool:
    return bool(_EXC_TITLE.search(re.sub(r"^\s*Điều\s+[\w.\-]+\.\s*", "", ten or "")))


def is_exception_article(ten: str | None, noidung: str | None) -> bool:
    """Điều có tiêu đề kiểu cấm/ngoại lệ, hoặc có ít nhất một khoản bắt đầu bằng "Không được / Cấm / …"."""
    return is_exception_title(ten) or any(is_exception_clause(t) for so, t in split_khoan(noidung) if so != "0")


_VBQ_MAIN = re.compile(rf"Điều\s+(\d+[a-zđ]?)\s+(.+?)\s+số\s+({SO_HIEU})")
_VBQ_DATE = re.compile(r"kể từ ngày\s+(\d{1,2})/(\d{1,2})/(\d{4})")
_VBQ_AMEND = re.compile(
    rf"((?:Điều\s+\d+[a-zđ]?\s*,?\s*)+?)\s*({DOC_TYPES}|Văn bản)\s+số\s+({SO_HIEU})"
    r"(?:[^;]*?kể từ ngày\s+(\d{1,2})/(\d{1,2})/(\d{4}))?"
)


def _iso(d: str, m: str, y: str) -> str:
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def parse_vbqppl(vbqppl: str | None) -> dict[str, Any] | None:
    """
    '(Điều 3 Nghị định số 112/2014/NĐ-CP, có hiệu lực thi hành kể từ ngày 15/01/2015, có nội dung được
      sửa đổi, có nội dung được bổ sung bởi Điều 1 Nghị định số 34/2023/NĐ-CP có hiệu lực … 31/07/2023)'
    → {so_dieu: '3', loai: 'Nghị định', so_hieu: '112/2014/NĐ-CP', hieu_luc_tu: '2015-01-15',
       hanh_vi: ['sua_doi', 'bo_sung'],
       sua_doi_boi: [{dieu: ['1'], loai: 'Nghị định', so_hieu: '34/2023/NĐ-CP', hieu_luc_tu: '2023-07-31'}]}
    hanh_vi ⊂ {sua_doi, bo_sung, bai_bo (cả điều), bai_bo_mot_phan}. Không nhận ra → None.
    """
    s = (vbqppl or "").strip()
    head, _, tail = s.partition(" bởi ")
    m = _VBQ_MAIN.search(head)
    if not m:
        return None
    d = _VBQ_DATE.search(head[m.end():])
    out: dict[str, Any] = {
        "so_dieu": m.group(1),
        "loai": m.group(2).strip(),
        "so_hieu": m.group(3),
        "hieu_luc_tu": _iso(*d.groups()) if d else None,
        "hanh_vi": [],
        "sua_doi_boi": [],
    }
    if not tail:
        return out
    acts = head[m.end():]
    if "sửa đổi" in acts:
        out["hanh_vi"].append("sua_doi")
    if "bổ sung" in acts:
        out["hanh_vi"].append("bo_sung")
    if "có nội dung bị bãi bỏ" in acts:
        out["hanh_vi"].append("bai_bo_mot_phan")
    elif "bị bãi bỏ" in acts:
        out["hanh_vi"].append("bai_bo")
    for part in tail.split(";"):
        a = _VBQ_AMEND.search(part)
        if a:
            out["sua_doi_boi"].append({
                "dieu": re.findall(r"\d+[a-zđ]?", a.group(1)),
                "loai": a.group(2),
                "so_hieu": a.group(3),
                "hieu_luc_tu": _iso(a.group(4), a.group(5), a.group(6)) if a.group(4) else None,
            })
    return out


# ── Dẫn chiếu trong nội dung ─────────────────────────────────────────────────
_LSEP = r"(?:\s*,\s*|\s+(?:và|hoặc)\s+)"
# "điểm a", "các điểm a, b, c và d", "khoản 1", "khoản 1, 2 và 3"
_UNIT = (rf"(?:(?:các\s+)?điểm\s+[a-zđ]\d?(?:{_LSEP}[a-zđ]\d?(?![\wĐđ]))*"
         rf"|(?:các\s+)?khoản\s+\d+[a-zđ]?(?:{_LSEP}\d+[a-zđ]?(?![\wĐđ]))*)")
_SEP = r"(?:\s*,\s*|\s+(?:và|hoặc)\s+|\s+)"
_UNIT_RE = re.compile(_UNIT)


def _units(pre: str) -> tuple[list[str], list[str]]:
    """'các điểm a, b và c khoản 2' → (khoản ['2'], điểm ['a', 'b', 'c'])."""
    khoan, diem = [], []
    for u in _UNIT_RE.finditer(pre):
        s = u.group(0)
        body = re.sub(r"^(?:các\s+)?(điểm|khoản)\s+", "", s)
        if "khoản" in s.split()[0:2]:
            khoan += re.findall(r"\d+[a-zđ]?", body)
        else:
            diem += re.findall(r"(?<![\wĐđ])([a-zđ]\d?)(?![\wĐđ])", body)
    return khoan, diem
_REF = re.compile(
    rf"(?P<pre>(?:{_UNIT}{_SEP})*)Điều\s+(?P<dieu>\d+[a-zđ]?)(?![\wĐđ])(?=(?P<after>[^.;:\n()]{{0,160}}))"
)
_QUAL = re.compile(rf"\s+(?:của\s+)?(?P<loai>{DOC_TYPES})(?P<rest>.*)", re.S)
_LIST_GAP = re.compile(r"^[\s,]*(?:và|hoặc)?[\s,]*$")


def extract_references(text: str | None) -> list[dict[str, Any]]:
    """
    Tìm dẫn chiếu "[điểm a, điểm b] [khoản 1 và khoản 2] Điều 6 [của Thông tư này | Nghị định số X | Luật Y]".
    Mỗi phần tử: {so_dieu, khoan[], diem[], loai_vb, so_hieu, ten_vb, pham_vi}
      pham_vi: 'noi_bo'  — "… này"                   → cùng văn bản với điều nguồn
               'ngoai'   — có số hiệu văn bản        → văn bản đó
               'ten'     — chỉ có tên ("Luật Tố cáo") → chưa giải được
               'khong_ro'— không ghi văn bản          → mặc định cùng văn bản
    Danh sách "Điều 11, Điều 12 Nghị định số 22/2019/NĐ-CP": Điều 11 thừa hưởng văn bản của Điều 12.
    Bỏ qua tiêu đề điều được trích nguyên văn ("“Điều 5. Phạm vi…").
    """
    text = text or ""
    refs: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    for m in _REF.finditer(text):
        pre = m.group("pre") or ""
        if not pre and text.startswith(".", m.end()):
            before = text[max(0, m.start() - 3):m.start()]
            if m.start() == 0 or "\n" in before or before.rstrip().endswith(("“", '"')):
                continue  # tiêu đề điều trích nguyên văn ("“Điều 5. …"), không phải dẫn chiếu
        khoan, diem = _units(pre)
        sent_start = max(text.rfind(c, 0, m.start()) for c in ".;\n") + 1
        context = text[max(sent_start, m.start() - 200):m.start()]
        ref: dict[str, Any] = {
            "so_dieu": m.group("dieu"),
            "khoan": khoan,
            "diem": diem,
            "loai_vb": None, "so_hieu": None, "ten_vb": None, "pham_vi": "khong_ro",
            "ngoai_le": bool(_EXC_CONTEXT.search(context)),
        }
        seg = re.split(r"Điều\s+\d|,", m.group("after"), maxsplit=1)[0]
        q = _QUAL.match(seg)
        if q:
            ref["loai_vb"] = q.group("loai")
            rest = q.group("rest")
            so = re.search(rf"\bsố\s+({SO_HIEU})", rest)
            if re.match(r"\s+này(?![\wĐđ])", rest):
                ref["pham_vi"] = "noi_bo"
            elif so:
                ref["pham_vi"], ref["so_hieu"] = "ngoai", so.group(1)
            else:
                ref["pham_vi"] = "ten"
                ref["ten_vb"] = re.sub(r"\s+(và|hoặc)\s*$", "", rest).strip() or None
        refs.append(ref)
        spans.append((m.start(), m.end()))
    # Danh sách dẫn chiếu: phần tử chưa ghi văn bản thừa hưởng phần tử ngay sau nếu chỉ cách nhau bởi , / và
    for i in range(len(refs) - 2, -1, -1):
        if refs[i]["pham_vi"] == "khong_ro" and _LIST_GAP.match(text[spans[i][1]:spans[i + 1][0]]):
            for k in ("loai_vb", "so_hieu", "ten_vb", "pham_vi"):
                refs[i][k] = refs[i + 1][k]
    return refs


def resolve_reference(ref: dict[str, Any], src_so_hieu: str | None,
                      lookup: dict[tuple[str, str], str]) -> str | None:
    """Dẫn chiếu → mapc qua bảng (số hiệu văn bản, số điều) → mapc. Không giải được → None."""
    if ref["pham_vi"] == "ngoai":
        doc = ref["so_hieu"]
    elif ref["pham_vi"] in ("noi_bo", "khong_ro"):
        doc = src_so_hieu
    else:
        return None
    return lookup.get((doc, ref["so_dieu"])) if doc else None


def build_lookup(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """(số hiệu, số điều) → mapc, lấy từ vbqppl. Trùng khoá → giữ mapc đầu tiên (rows nên sắp theo mapc)."""
    out: dict[tuple[str, str], str] = {}
    for r in rows:
        p = parse_vbqppl(r.get("vbqppl"))
        if p:
            out.setdefault((p["so_hieu"], p["so_dieu"]), r["mapc"])
    return out


def reference_edges(rows: Iterable[dict[str, Any]], lookup: dict[tuple[str, str], str]) -> list[dict[str, Any]]:
    """Cạnh THAM_CHIEU (gộp theo cặp nguồn→đích): [{src, tgt, khoan[], diem[], pham_vi}]."""
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        src = r["mapc"]
        p = parse_vbqppl(r.get("vbqppl"))
        src_doc = p["so_hieu"] if p else None
        for ref in extract_references(r.get("noidung")):
            tgt = resolve_reference(ref, src_doc, lookup)
            if not tgt or tgt == src:
                continue
            e = edges.setdefault((src, tgt), {"src": src, "tgt": tgt, "khoan": [], "diem": [],
                                              "pham_vi": ref["pham_vi"], "ngoai_le": False})
            e["ngoai_le"] = e["ngoai_le"] or ref["ngoai_le"]
            e["khoan"] = sorted(set(e["khoan"]) | set(ref["khoan"]), key=_num_key)
            e["diem"] = sorted(set(e["diem"]) | set(ref["diem"]))
    return list(edges.values())


def reference_keys(rows: Iterable[dict[str, Any]]) -> set[tuple[str, str]]:
    """Các khoá (số hiệu, số điều) mà nội dung các điều này dẫn chiếu tới — để tra mapc từ đồ thị."""
    keys: set[tuple[str, str]] = set()
    for r in rows:
        p = parse_vbqppl(r.get("vbqppl"))
        for ref in extract_references(r.get("noidung")):
            doc = ref["so_hieu"] if ref["pham_vi"] == "ngoai" else (
                p["so_hieu"] if p and ref["pham_vi"] in ("noi_bo", "khong_ro") else None)
            if doc:
                keys.add((doc, ref["so_dieu"]))
    return keys


def _num_key(s: str) -> tuple[int, str]:
    m = re.match(r"(\d+)(.*)", s)
    return (int(m.group(1)), m.group(2)) if m else (10**9, s)


# ── Khoản ────────────────────────────────────────────────────────────────────
_KHOAN_START = re.compile(r"(?m)^[ \t]*(\d{1,3})\.\s")


def split_khoan(text: str | None) -> list[tuple[str, str]]:
    """
    Tách nội dung điều thành khoản theo dòng bắt đầu bằng "1. ", "2. "… (đánh số tăng dần liên tục —
    số lạc thứ tự, vd. danh sách đánh số bên trong một điểm, không bị coi là khoản mới).
    Phần trước khoản 1 (nếu có) trả với số "0". Không có khoản → [("0", toàn văn)].
    """
    text = (text or "").strip()
    if not text:
        return []
    parts: list[tuple[str, str]] = []
    cur, last, expected = "0", 0, 1
    for m in _KHOAN_START.finditer(text):
        if int(m.group(1)) != expected:
            continue
        seg = text[last:m.start()].strip()
        if seg:
            parts.append((cur, seg))
        cur, last, expected = m.group(1), m.start(), expected + 1
    parts.append((cur, text[last:].strip()))
    return [(s, t) for s, t in parts if t]


def head_truncate(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n… [đã rút gọn]"


def select_relevant_text(text: str | None, query: str, max_chars: int) -> tuple[str, bool]:
    """
    Rút gọn nội dung điều dài cho LLM theo TRUY VẤN thay vì cắt phần đầu:
    giữ phần mở đầu + các khoản khớp truy vấn nhất (độ phủ âm tiết/bigram), theo thứ tự gốc, đánh dấu
    chỗ lược. Không có cấu trúc khoản hoặc không có truy vấn → cắt phần đầu như cũ.
    Trả (văn bản, đã_rút_gọn).
    """
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text, False
    parts = split_khoan(text)
    khoan = [p for p in parts if p[0] != "0"]
    if len(khoan) < 2 or not (query or "").strip():
        return head_truncate(text, max_chars), True
    from retrieve.lexical_bm25 import tokenize

    q = set(tokenize(query))
    if not q:
        return head_truncate(text, max_chars), True

    def score(t: str) -> float:
        toks = set(tokenize(t))
        return sum(2.0 if "_" in x else 1.0 for x in q if x in toks)

    preamble = parts[0][1][:300] if parts[0][0] == "0" else ""
    budget = max_chars - len(preamble) - 120
    scored = {so: score(t) for so, t in khoan}
    best = max(scored.values())
    # Thứ tự ưu tiên: khoản khớp truy vấn nhất → khoản ngoại lệ/cấm ("Không được…", "Trừ trường hợp…": thường
    # không trùng từ với câu hỏi nhưng làm đổi kết luận) → các khoản còn lại theo độ khớp.
    def tier(p: tuple[str, str]) -> int:
        if best > 0 and scored[p[0]] == best:
            return 0
        return 1 if is_exception_clause(p[1]) else 2

    chosen: dict[str, str] = {}
    used = 0
    for so, t in sorted(khoan, key=lambda p: (tier(p), -scored[p[0]], _num_key(p[0]))):
        if used + len(t) + 6 <= budget:
            chosen[so], used = t, used + len(t) + 6
        elif not chosen:  # khoản khớp nhất dài hơn cả ngân sách → cắt khoản đó
            chosen[so], used = head_truncate(t, budget), budget
    out, prev = ([preamble] if preamble else []), 0
    for i, (so, _) in enumerate(khoan, start=1):
        if so in chosen:
            if i - prev > 1:
                out.append("[…]")
            out.append(chosen[so])
            prev = i
    if prev < len(khoan):
        out.append("[…]")
    omitted = [so for so, _ in khoan if so not in chosen]
    out.append(f"[Đã lược khoản {', '.join(omitted)} — gọi get_full_article để đọc toàn văn]")
    return "\n".join(out), True


# ═════════════════════════════════════════════════════════════════════════════
# 2. Đọc MySQL
# ═════════════════════════════════════════════════════════════════════════════
_ARTICLE_COLS = "mapc, ten, noidung, chimuc, stt, chuong_id, demuc_id, chude_id, vbqppl"


def article_rows(conn, mapcs: Iterable[str] | None = None) -> list[dict[str, Any]]:
    import pymysql

    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        if mapcs is None:
            cur.execute(f"SELECT {_ARTICLE_COLS} FROM pddieu ORDER BY mapc")
        else:
            mapcs = list(mapcs)
            if not mapcs:
                return []
            cur.execute(f"SELECT {_ARTICLE_COLS} FROM pddieu WHERE mapc IN ({','.join(['%s'] * len(mapcs))}) "
                        "ORDER BY mapc", mapcs)
        return list(cur.fetchall())


def _dict_rows(conn, sql: str, args: Iterable | None = None) -> list[dict[str, Any]]:
    import pymysql

    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(sql, list(args or []))
        return list(cur.fetchall())


# ═════════════════════════════════════════════════════════════════════════════
# 3. Ghi Neo4j
# ═════════════════════════════════════════════════════════════════════════════
SCHEMA = [
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:ChuDe)  REQUIRE n.id      IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:DeMuc)  REQUIRE n.id      IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Chuong) REQUIRE n.mapc    IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Dieu)   REQUIRE n.mapc    IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Khoan)  REQUIRE n.id      IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:VanBan) REQUIRE n.so_hieu IS UNIQUE",
    "CREATE INDEX dieu_so_hieu IF NOT EXISTS FOR (n:Dieu) ON (n.so_hieu, n.so_dieu)",
    "CREATE INDEX khoan_mapc IF NOT EXISTS FOR (n:Khoan) ON (n.mapc)",
    "CREATE FULLTEXT INDEX dieu_fulltext IF NOT EXISTS FOR (n:Dieu) ON EACH [n.ten, n.noidung]",
]


def _run(driver, cypher: str, **params) -> list[dict[str, Any]]:
    with driver.session() as s:
        return [dict(r) for r in s.run(cypher, **params)]


def _batched(driver, cypher: str, items: list, size: int = BATCH, key: str = "batch") -> None:
    for i in range(0, len(items), size):
        _run(driver, cypher, **{key: items[i:i + size]})


def ensure_schema(driver) -> None:
    for c in SCHEMA:
        try:
            _run(driver, c)
        except Exception as e:  # index đã tồn tại với tên khác… không chặn build
            print(f"[graph] schema: {e}")


def write_hierarchy(driver, conn, rows: list[dict[str, Any]] | None = None) -> None:
    """ChuDe/DeMuc/Chuong + cạnh phân cấp. rows=None → toàn bộ; có rows → chỉ phần các điều đó dùng."""
    if rows is None:
        chude = _dict_rows(conn, "SELECT id, ten, stt FROM pdchude")
        demuc = _dict_rows(conn, "SELECT id, ten, stt, chude_id FROM pddemuc")
        chuong = _dict_rows(conn, "SELECT mapc, ten, chimuc, stt, demuc_id FROM pdchuong")
    else:
        ch_ids = sorted({r["chuong_id"] for r in rows if r.get("chuong_id")})
        dm_ids = sorted({r["demuc_id"] for r in rows if r.get("demuc_id")})
        cd_ids = sorted({r["chude_id"] for r in rows if r.get("chude_id")})
        ph = lambda xs: ",".join(["%s"] * len(xs))  # noqa: E731
        chuong = _dict_rows(conn, f"SELECT mapc, ten, chimuc, stt, demuc_id FROM pdchuong WHERE mapc IN ({ph(ch_ids)})",
                            ch_ids) if ch_ids else []
        demuc = _dict_rows(conn, f"SELECT id, ten, stt, chude_id FROM pddemuc WHERE id IN ({ph(dm_ids)})",
                           dm_ids) if dm_ids else []
        cd_ids = sorted(set(cd_ids) | {d["chude_id"] for d in demuc if d.get("chude_id")})
        chude = _dict_rows(conn, f"SELECT id, ten, stt FROM pdchude WHERE id IN ({ph(cd_ids)})",
                           cd_ids) if cd_ids else []
    _batched(driver, "UNWIND $batch AS r MERGE (n:ChuDe {id: r.id}) SET n.ten = r.ten, n.stt = r.stt", chude)
    _batched(driver, """UNWIND $batch AS r MERGE (n:DeMuc {id: r.id}) SET n.ten = r.ten, n.stt = r.stt
                        WITH n, r MATCH (cd:ChuDe {id: r.chude_id}) MERGE (cd)-[:CO_DE_MUC]->(n)""", demuc)
    _batched(driver, """UNWIND $batch AS r MERGE (n:Chuong {mapc: r.mapc})
                        SET n.ten = r.ten, n.chimuc = r.chimuc, n.stt = r.stt
                        WITH n, r MATCH (dm:DeMuc {id: r.demuc_id}) MERGE (dm)-[:CO_CHUONG]->(n)""", chuong)


def _dieu_payload(r: dict[str, Any]) -> dict[str, Any]:
    p = parse_vbqppl(r.get("vbqppl")) or {}
    return {
        "mapc": r["mapc"], "ten": r.get("ten"), "noidung": r.get("noidung"), "vbqppl": r.get("vbqppl"),
        "chimuc": r.get("chimuc"), "stt": r.get("stt"), "chuong_id": r.get("chuong_id"),
        "demuc_id": r.get("demuc_id"), "chude_id": r.get("chude_id"),
        "so_hieu": p.get("so_hieu"), "so_dieu": p.get("so_dieu"),
        "ngoai_le": is_exception_article(r.get("ten"), r.get("noidung")),
    }


def write_dieu(driver, rows: list[dict[str, Any]]) -> None:
    """MERGE Dieu (kèm vbqppl, số hiệu, số điều) + nối lại Chuong."""
    _batched(driver, """
        UNWIND $batch AS r
        MERGE (d:Dieu {mapc: r.mapc})
        SET d.ten = r.ten, d.noidung = r.noidung, d.vbqppl = r.vbqppl, d.chimuc = r.chimuc, d.stt = r.stt,
            d.demuc_id = r.demuc_id, d.chude_id = r.chude_id, d.so_hieu = r.so_hieu, d.so_dieu = r.so_dieu,
            d.ngoai_le = r.ngoai_le
        WITH d, r
        OPTIONAL MATCH (:Chuong)-[old:CO_DIEU]->(d)
        DELETE old
        WITH DISTINCT d, r
        MATCH (ch:Chuong {mapc: r.chuong_id})
        MERGE (ch)-[:CO_DIEU]->(d)
    """, [_dieu_payload(r) for r in rows])


def khoan_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        parts = [p for p in split_khoan(r.get("noidung")) if p[0] != "0"]
        if len(parts) < 2:
            continue
        out += [{"id": f"{r['mapc']}#{so}", "mapc": r["mapc"], "so": so, "noidung": t,
                 "ngoai_le": is_exception_clause(t)} for so, t in parts]
    return out


def write_khoan(driver, rows: list[dict[str, Any]], *, fresh: bool = False) -> int:
    """Node Khoan cho điều có ≥ 2 khoản. fresh=False → xoá khoản cũ của các điều này trước (đồng bộ)."""
    if not fresh:
        _batched(driver, "UNWIND $batch AS m MATCH (k:Khoan {mapc: m}) DETACH DELETE k",
                 [r["mapc"] for r in rows])
    payload = khoan_payload(rows)
    _batched(driver, """
        UNWIND $batch AS k
        MATCH (d:Dieu {mapc: k.mapc})
        MERGE (n:Khoan {id: k.id}) SET n.mapc = k.mapc, n.so = k.so, n.noidung = k.noidung, n.ngoai_le = k.ngoai_le
        MERGE (d)-[:CO_KHOAN]->(n)
    """, payload, size=KHOAN_BATCH)
    return len(payload)


_HANH_VI_TEN = {"sua_doi": "sửa đổi", "bo_sung": "bổ sung", "bai_bo": "bãi bỏ", "bai_bo_mot_phan": "bãi bỏ một phần"}


def vanban_payload(rows: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """→ (điều thuộc văn bản, điều được sửa đổi/bãi bỏ bởi văn bản)."""
    thuoc, sua = [], []
    for r in rows:
        p = parse_vbqppl(r.get("vbqppl"))
        if not p:
            continue
        thuoc.append({"mapc": r["mapc"], "so_hieu": p["so_hieu"], "loai": p["loai"],
                      "hieu_luc_tu": p["hieu_luc_tu"], "so_dieu": p["so_dieu"]})
        for a in p["sua_doi_boi"]:
            sua.append({"mapc": r["mapc"], "goc": p["so_hieu"], "so_hieu": a["so_hieu"], "loai": a["loai"],
                        "hieu_luc_tu": a["hieu_luc_tu"], "dieu": a["dieu"], "hanh_vi": p["hanh_vi"]})
    return thuoc, sua


def write_vanban(driver, rows: list[dict[str, Any]], *, fresh: bool = False) -> int:
    """VanBan + THUOC + DUOC_SUA_DOI_BOI (điều) + SUA_DOI_BO_SUNG (văn bản → văn bản)."""
    if not fresh:
        _batched(driver, """UNWIND $batch AS m MATCH (d:Dieu {mapc: m})-[e:THUOC|DUOC_SUA_DOI_BOI]->() DELETE e""",
                 [r["mapc"] for r in rows])
    thuoc, sua = vanban_payload(rows)
    _batched(driver, """
        UNWIND $batch AS r
        MATCH (d:Dieu {mapc: r.mapc})
        MERGE (v:VanBan {so_hieu: r.so_hieu})
        SET v.loai = coalesce(v.loai, r.loai), v.hieu_luc_tu = coalesce(v.hieu_luc_tu, r.hieu_luc_tu)
        MERGE (d)-[t:THUOC]->(v) SET t.so_dieu = r.so_dieu
    """, thuoc)
    _batched(driver, """
        UNWIND $batch AS r
        MATCH (d:Dieu {mapc: r.mapc})
        MERGE (v:VanBan {so_hieu: r.so_hieu}) SET v.loai = coalesce(v.loai, r.loai)
        MERGE (d)-[e:DUOC_SUA_DOI_BOI]->(v)
        SET e.hanh_vi = r.hanh_vi, e.dieu = r.dieu, e.hieu_luc_tu = r.hieu_luc_tu
        WITH v, r
        MATCH (g:VanBan {so_hieu: r.goc})
        MERGE (v)-[:SUA_DOI_BO_SUNG]->(g)
    """, sua)
    return len(thuoc)


def write_admin_relations(driver, conn) -> int:
    """Sổ cái văn bản do admin duyệt (bảng vanban / vanban_quanhe) → trạng thái + cạnh giữa các VanBan."""
    try:
        vb = _dict_rows(conn, "SELECT so_hieu, loai, trang_thai, hieu_luc_tu, hieu_luc_den, thay_the_boi FROM vanban")
        qh = _dict_rows(conn, "SELECT so_hieu, loai, doi_tuong FROM vanban_quanhe")
    except Exception as e:  # chưa có bảng sổ cái
        print(f"[graph] Bỏ qua sổ cái văn bản: {e}")
        return 0
    for r in vb:
        for k in ("hieu_luc_tu", "hieu_luc_den"):
            r[k] = r[k].isoformat() if r.get(k) else None
    _batched(driver, """
        UNWIND $batch AS r
        MERGE (v:VanBan {so_hieu: r.so_hieu})
        SET v.loai = coalesce(r.loai, v.loai), v.trang_thai = r.trang_thai,
            v.hieu_luc_tu = coalesce(r.hieu_luc_tu, v.hieu_luc_tu), v.hieu_luc_den = r.hieu_luc_den
        WITH v, r WHERE r.thay_the_boi IS NOT NULL AND r.thay_the_boi <> ''
        MERGE (m:VanBan {so_hieu: r.thay_the_boi})
        MERGE (m)-[:THAY_THE]->(v)
    """, vb)
    rel_type = {"sua_doi": "SUA_DOI_BO_SUNG", "bai_bo": "BAI_BO", "het_hieu_luc": "BAI_BO",
                "hop_nhat": "HOP_NHAT", "thay_the": "THAY_THE"}
    n = 0
    for loai, typ in rel_type.items():
        items = [r for r in qh if r["loai"] == loai]
        n += len(items)
        _batched(driver, f"""
            UNWIND $batch AS r
            MERGE (a:VanBan {{so_hieu: r.so_hieu}})
            MERGE (b:VanBan {{so_hieu: r.doi_tuong}})
            MERGE (a)-[:{typ}]->(b)
        """, items)
    return len(vb) + n


def write_thamchieu(driver, edges: list[dict[str, Any]], *, sources: Iterable[str] | None = None) -> int:
    """Ghi cạnh THAM_CHIEU. sources → xoá cạnh đi ra cũ của các điều này trước (đồng bộ)."""
    if sources is not None:
        _batched(driver, "UNWIND $batch AS m MATCH (:Dieu {mapc: m})-[e:THAM_CHIEU]->() DELETE e", list(sources))
    _batched(driver, """
        UNWIND $batch AS e
        MATCH (a:Dieu {mapc: e.src})
        MATCH (b:Dieu {mapc: e.tgt})
        MERGE (a)-[r:THAM_CHIEU]->(b)
        SET r.khoan = e.khoan, r.diem = e.diem, r.pham_vi = e.pham_vi, r.ngoai_le = e.ngoai_le
    """, edges, size=2000)
    return len(edges)


def clear_thamchieu(driver) -> None:
    """Xoá toàn bộ THAM_CHIEU (build lại parser mới) — theo lô để không tràn bộ nhớ giao dịch."""
    while True:
        rows = _run(driver, "MATCH ()-[e:THAM_CHIEU]->() WITH e LIMIT 20000 DELETE e RETURN count(*) AS n")
        if not rows or not rows[0]["n"]:
            break


def lookup_from_graph(driver, keys: Iterable[tuple[str, str]]) -> dict[tuple[str, str], str]:
    keys = [list(k) for k in keys]
    out: dict[tuple[str, str], str] = {}
    for i in range(0, len(keys), 2000):
        for r in _run(driver, """
            UNWIND $keys AS k
            MATCH (d:Dieu {so_hieu: k[0], so_dieu: k[1]})
            RETURN k[0] AS so_hieu, k[1] AS so_dieu, min(d.mapc) AS mapc
        """, keys=keys[i:i + 2000]):
            out[(r["so_hieu"], r["so_dieu"])] = r["mapc"]
    return out


def delete_dieu(driver, mapcs: Iterable[str]) -> int:
    mapcs = [m for m in mapcs if m]
    _batched(driver, """UNWIND $batch AS m
                        OPTIONAL MATCH (k:Khoan {mapc: m}) DETACH DELETE k
                        WITH DISTINCT m MATCH (d:Dieu {mapc: m}) DETACH DELETE d""", mapcs)
    return len(mapcs)


def sync_mapcs(driver, conn, mapcs: Iterable[str], deleted: Iterable[str] = ()) -> dict[str, int]:
    """
    Đồng bộ từng phần sau khi MySQL đổi (admin duyệt văn bản / cập nhật pháp điển):
    phân cấp → Dieu → Khoan → VanBan → THAM_CHIEU đi ra của các điều này; xoá điều đã bị xoá khỏi MySQL.
    (Dẫn chiếu TỪ điều khác TỚI điều mới chỉ có khi build lại toàn bộ.)
    """
    mapcs = list(dict.fromkeys(m for m in mapcs if m))
    deleted = [m for m in deleted if m]
    rows = article_rows(conn, mapcs)
    if rows:
        ensure_schema(driver)
        write_hierarchy(driver, conn, rows)
        write_dieu(driver, rows)
        n_k = write_khoan(driver, rows)
        write_vanban(driver, rows)
        lookup = lookup_from_graph(driver, reference_keys(rows))
        n_e = write_thamchieu(driver, reference_edges(rows, lookup), sources=[r["mapc"] for r in rows])
        write_admin_relations(driver, conn)
    else:
        n_k = n_e = 0
    if deleted:
        delete_dieu(driver, deleted)
    return {"graph_dieu": len(rows), "graph_khoan": n_k, "graph_tham_chieu": n_e, "graph_deleted": len(deleted)}


def mark_exceptions(driver, rows: list[dict[str, Any]]) -> int:
    """Gắn cờ ngoai_le cho Dieu/Khoan trên đồ thị đã có (không build lại toàn bộ)."""
    dieu = [{"mapc": r["mapc"], "ngoai_le": is_exception_article(r.get("ten"), r.get("noidung"))} for r in rows]
    _batched(driver, "UNWIND $batch AS r MATCH (d:Dieu {mapc: r.mapc}) SET d.ngoai_le = r.ngoai_le", dieu, size=5000)
    khoan = [{"id": k["id"], "ngoai_le": k["ngoai_le"]} for k in khoan_payload(rows)]
    _batched(driver, "UNWIND $batch AS k MATCH (n:Khoan {id: k.id}) SET n.ngoai_le = k.ngoai_le", khoan, size=5000)
    return sum(d["ngoai_le"] for d in dieu)


def graph_stats(driver) -> dict[str, int]:
    rows = _run(driver, """
        CALL { MATCH (d:Dieu) RETURN count(d) AS dieu }
        CALL { MATCH (k:Khoan) RETURN count(k) AS khoan }
        CALL { MATCH (v:VanBan) RETURN count(v) AS van_ban }
        CALL { MATCH ()-[e:THAM_CHIEU]->() RETURN count(e) AS tham_chieu }
        CALL { MATCH ()-[e:LIEN_QUAN]->() RETURN count(e) AS lien_quan }
        RETURN dieu, khoan, van_ban, tham_chieu, lien_quan
    """)
    return rows[0] if rows else {}
