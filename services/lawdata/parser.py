"""
Chữ của một văn bản pháp luật → metadata + cấu trúc theo ĐÚNG văn bản đó.

Cấp nào văn bản có thì nhận (Phần, Chương, Mục, Điều; Khoản/Điểm nằm trong nội dung điều),
cấp nào không có thì để trống — không ép theo chủ đề/đề mục pháp điển.

Nội dung trong ngoặc kép “…” (văn bản sửa đổi trích lại điều/khoản cũ) KHÔNG được coi là
cấu trúc của văn bản đang đọc.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# ── Mẫu nhận diện ────────────────────────────────────────────────────────────
SO_HIEU = r"\d{1,4}[a-z]?/\d{4}/[A-ZĐ][A-ZĐ0-9]*(?:-[A-ZĐ0-9]+)*"
SO_HIEU_RE = re.compile(rf"\b({SO_HIEU})\b")
_HEADER_SO = re.compile(
    rf"(?:Số|Luật số|Bộ luật số|Nghị quyết số|Pháp lệnh số|Lệnh số)\s*:\s*({SO_HIEU})", re.IGNORECASE)
_DATE = re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})", re.IGNORECASE)
_HIEU_LUC = re.compile(
    r"có hiệu lực(?: thi hành)?(?: kể)? từ ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})", re.IGNORECASE)

LOAI_VB = [  # dài trước ngắn sau để "THÔNG TƯ LIÊN TỊCH" không bị nhận thành "THÔNG TƯ"
    "BỘ LUẬT", "LUẬT", "PHÁP LỆNH", "NGHỊ QUYẾT LIÊN TỊCH", "NGHỊ QUYẾT", "NGHỊ ĐỊNH",
    "THÔNG TƯ LIÊN TỊCH", "THÔNG TƯ", "QUYẾT ĐỊNH", "CHỈ THỊ", "LỆNH", "HIẾN PHÁP",
]
_CO_QUAN_THEO_SO = {
    "QH": "Quốc hội", "UBTVQH": "Ủy ban Thường vụ Quốc hội", "NĐ-CP": "Chính phủ",
    "NQ-CP": "Chính phủ", "QĐ-TTg": "Thủ tướng Chính phủ", "CT-TTg": "Thủ tướng Chính phủ",
}

_PHAN = re.compile(r"^PH[ẦA]N\s+(TH[ỨU]\s+[A-ZÀ-Ỹ]+|[IVXLC]+)\b\.?\s*(.*)$", re.IGNORECASE)
_CHUONG = re.compile(r"^CH[ƯU]ƠNG\s+([IVXLC]+|\d+)\b\.?\s*(.*)$", re.IGNORECASE)
_MUC = re.compile(r"^M[ỤU]C\s+(\d+)\b\.?\s*(.*)$", re.IGNORECASE)
_DIEU = re.compile(r"^Điều\s+(\d+)([a-zđ]?)\s*\.\s*(.*)$")
_KHOAN = re.compile(r"^\d+[a-z]?\.\s+\S")
_DIEM = re.compile(r"^[a-zđ](?:\d)?\)\s+\S")
_PAGE_HEADER = re.compile(r"^CÔNG BÁO\s*/.*$|^\d{1,4}$")
_END = re.compile(
    r"^(Nơi nhận\s*:|TM\.\s|KT\.\s|Q\.\s|TL\.\s|XÁC THỰC VĂN BẢN HỢP NHẤT|"
    r"CHỦ TỊCH QUỐC HỘI$|CHỦ TỊCH NƯỚC$|THỦ TƯỚNG$|BỘ TRƯỞNG$|"
    r"(Luật|Bộ luật|Pháp lệnh|Nghị quyết) này (đã )?được .* thông qua)")


@dataclass
class Article:
    so: str                    # "21", "21a"
    ten: str                   # tên điều (không kèm "Điều 21.")
    noidung: str
    phan: str = ""
    chuong: str = ""           # "Chương III. CẤP, QUẢN LÝ THẺ CĂN CƯỚC"
    muc: str = ""

    @property
    def tieu_de(self) -> str:
        return f"Điều {self.so}. {self.ten}".strip()


@dataclass
class Relation:
    loai: str                  # sua_doi | bai_bo | het_hieu_luc | hop_nhat
    so_hieu: str               # văn bản bị tác động
    trich: str = ""            # câu gốc làm căn cứ, để người duyệt kiểm tra
    xac_nhan: bool = False     # người duyệt tích chọn mới áp dụng


@dataclass
class ParsedDoc:
    so_hieu: str = ""
    loai: str = ""             # "Luật", "Thông tư", …
    trich_yeu: str = ""
    co_quan: str = ""
    ngay_ban_hanh: str = ""    # YYYY-MM-DD
    hieu_luc_tu: str = ""
    is_vbhn: bool = False
    goc_so_hieu: str = ""      # văn bản hợp nhất → văn bản gốc
    articles: list[Article] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ten_day_du(self) -> str:
        head = f"{self.loai} số {self.so_hieu}".strip() if self.so_hieu else self.loai
        return f"{head} {self.trich_yeu}".strip()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ten_day_du"] = self.ten_day_du
        return d


def _iso(m: re.Match) -> str:
    d, mth, y = (int(x) for x in m.groups())
    return f"{y:04d}-{mth:02d}-{d:02d}"


def _cap(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    if s.isupper():
        s = s.lower()
    return s[:1].upper() + s[1:]


def _is_caps(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.85


def clean_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        ln = re.sub(r"[ \t]+", " ", raw).strip()
        if not ln or _PAGE_HEADER.match(ln):
            continue
        lines.append(ln)
    return lines


# ── Phần đầu văn bản ─────────────────────────────────────────────────────────
def _parse_header(lines: list[str], doc: ParsedDoc) -> int:
    """Điền metadata từ phần đầu; trả chỉ số dòng bắt đầu phần thân (Căn cứ…/Chương/Điều)."""
    body_start = len(lines)
    for i, ln in enumerate(lines[:80]):
        if ln.startswith("Căn cứ") or _DIEU.match(ln) or _CHUONG.match(ln) or _PHAN.match(ln):
            body_start = i
            break
    header = lines[:body_start]
    joined = "\n".join(header)

    if m := _HEADER_SO.search(joined):
        doc.so_hieu = m.group(1)
    elif m := SO_HIEU_RE.search(joined):
        doc.so_hieu = m.group(1)
    doc.is_vbhn = "VBHN" in doc.so_hieu or "VĂN BẢN HỢP NHẤT" in joined

    if header and "CỘNG HÒA" in header[0]:
        cq = re.sub(r"\s+", " ", header[0].split("CỘNG HÒA")[0]).strip()
        # "BỘ TÀI CHÍNH" → "Bộ Tài chính" (chữ đầu của tên riêng viết hoa)
        m = re.match(r"^(BỘ|ỦY BAN NHÂN DÂN|HỘI ĐỒNG NHÂN DÂN|NGÂN HÀNG|TÒA ÁN|VIỆN)\s+(.*)$", cq)
        doc.co_quan = f"{_cap(m.group(1))} {_cap(m.group(2))}" if m else _cap(cq)
    for suffix, name in _CO_QUAN_THEO_SO.items():
        if not doc.co_quan and re.search(rf"/{re.escape(suffix)}\d*$", doc.so_hieu):
            doc.co_quan = name

    for ln in header:
        if "ngày" in ln and (m := _DATE.search(ln)):
            doc.ngay_ban_hanh = _iso(m)
            break

    # Loại văn bản = dòng IN HOA đúng tên loại; trích yếu = các dòng ngay sau nó
    loai_idx = None
    for i, ln in enumerate(header):
        name = ln.strip().upper()
        if name in LOAI_VB:
            doc.loai = _cap(name)
            loai_idx = i
            break
    if loai_idx is not None:
        parts = []
        for ln in header[loai_idx + 1: loai_idx + 6]:
            if re.match(rf"^({'|'.join(x.title() for x in LOAI_VB)}|Luật|Nghị định|Thông tư)\s+số\b", ln) \
                    or ln.startswith(("Căn cứ", "Theo đề nghị")):
                break
            parts.append(ln)
        doc.trich_yeu = _cap(" ".join(parts))
        if doc.loai in ("Luật", "Bộ luật", "Pháp lệnh") and doc.trich_yeu:
            doc.trich_yeu = doc.trich_yeu[:1].lower() + doc.trich_yeu[1:] if doc.trich_yeu.startswith(("Sửa", "Bổ")) \
                else doc.trich_yeu
    if not doc.loai:
        doc.warnings.append("Không nhận ra loại văn bản — điền tay trước khi duyệt.")

    if doc.is_vbhn:
        # "Thông tư số 39/2025/TT-BKHCN ngày … được sửa đổi, bổ sung bởi:" ngay sau trích yếu
        for ln in lines[loai_idx or 0: (loai_idx or 0) + 15]:
            if m := re.match(rf"^(?:{'|'.join(x.title() for x in LOAI_VB)}|Luật|Nghị định|Thông tư)\s+số\s+({SO_HIEU})", ln):
                if "VBHN" not in m.group(1):
                    doc.goc_so_hieu = m.group(1)
                    doc.relations.append(Relation("hop_nhat", m.group(1), ln[:200], xac_nhan=True))  # lấy từ phần đầu VBHN → chắc chắn
                    break
        if not doc.goc_so_hieu:
            doc.warnings.append("Văn bản hợp nhất nhưng không tìm thấy số hiệu văn bản gốc — điền tay.")
    return body_start


# ── Thân văn bản ─────────────────────────────────────────────────────────────
def _quote_delta(line: str) -> int:
    return line.count("“") - line.count("”")


def _paragraphs(lines: list[str]) -> str:
    """Ghép các dòng bị ngắt theo khổ giấy thành đoạn; khoản cách nhau một dòng trống."""
    paras: list[str] = []
    for ln in lines:
        new_para = (
            not paras or _KHOAN.match(ln) or _DIEM.match(ln) or ln.startswith(("“", "\"", "-", "+"))
            or (paras[-1].rstrip().endswith((".", ":", ";", "”", "”;", "”.")) and ln[:1].isupper())
        )
        if new_para:
            paras.append(ln)
        else:
            paras[-1] += " " + ln
    out = []
    for p in paras:
        out.append(("\n" if out else "") + p if _KHOAN.match(p) and out else p)
    return "\n".join(out).strip()


def _parse_body(lines: list[str], doc: ParsedDoc) -> None:
    depth = 0
    phan = chuong = muc = ""
    cur: Optional[dict] = None
    pending_title: Optional[list] = None   # [kind, label, parts]
    last_num = 0
    preamble_done = False

    def flush():
        nonlocal cur
        if cur:
            doc.articles.append(Article(cur["so"], _cap(cur["ten"]) if cur["ten"].isupper() else cur["ten"].strip(),
                                        _paragraphs(cur["body"]), phan=cur["phan"], chuong=cur["chuong"], muc=cur["muc"]))
        cur = None

    for ln in lines:
        at_top = depth <= 0
        depth = max(0, depth + _quote_delta(ln))

        if at_top and _END.match(ln) and doc.articles + ([cur] if cur else []):
            break

        # Tên của Phần/Chương/Mục: các dòng IN HOA ngay sau tiêu đề
        if pending_title is not None:
            if at_top and _is_caps(ln) and not _DIEU.match(ln) and len(pending_title[2]) < 4:
                pending_title[2].append(ln)
                continue
            kind, label, parts = pending_title
            title = f"{label}. {' '.join(parts)}".strip(". ") if parts else label
            if kind == "phan":
                phan, chuong, muc = title, "", ""
            elif kind == "chuong":
                chuong, muc = title, ""
            else:
                muc = title
            pending_title = None

        if at_top:
            if m := _PHAN.match(ln):
                flush(); preamble_done = True
                pending_title = ["phan", f"Phần {m.group(1).lower()}", [m.group(2)] if m.group(2) else []]
                continue
            if m := _CHUONG.match(ln):
                flush(); preamble_done = True
                pending_title = ["chuong", f"Chương {m.group(1).upper()}", [m.group(2)] if m.group(2) else []]
                continue
            if (m := _MUC.match(ln)) and (m.group(2) == "" or _is_caps(m.group(2))):
                flush(); preamble_done = True
                pending_title = ["muc", f"Mục {m.group(1)}", [m.group(2)] if m.group(2) else []]
                continue
            if m := _DIEU.match(ln):
                num = int(m.group(1))
                if num > last_num or (num == last_num and m.group(2)):
                    if last_num and num > last_num + 1:
                        doc.warnings.append(f"Thiếu điều: nhảy từ Điều {last_num} sang Điều {num}.")
                    flush(); preamble_done = True
                    last_num = num
                    cur = {"so": f"{num}{m.group(2)}", "ten": m.group(3), "body": [], "phan": phan,
                           "chuong": chuong, "muc": muc, "title_open": not m.group(3).endswith(".")}
                    continue
        if not preamble_done or cur is None:
            continue
        # Tên điều dài bị ngắt dòng: dòng sau bắt đầu bằng chữ thường
        if cur["title_open"] and not cur["body"] and ln[:1].islower() and at_top:
            cur["ten"] += " " + ln
            continue
        cur["title_open"] = False
        cur["body"].append(ln)
    flush()


# ── Quan hệ với văn bản khác (gợi ý cho người duyệt) ─────────────────────────
def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.;:])\s+", text) if s.strip()]


def _find_relations(doc: ParsedDoc, preamble: str) -> None:
    seen = {(r.loai, r.so_hieu) for r in doc.relations}

    def add(loai: str, so: str, trich: str):
        if so and so != doc.so_hieu and so != doc.goc_so_hieu and (loai, so) not in seen:
            seen.add((loai, so))
            doc.relations.append(Relation(loai, so, trich[:300]))

    if not doc.is_vbhn:
        # Văn bản sửa đổi: số hiệu trong câu "ban hành … sửa đổi, bổ sung …" (không lấy "Căn cứ …")
        for s in _sentences(preamble + " " + doc.trich_yeu):
            if "sửa đổi" in s.lower() and not s.startswith("Căn cứ"):
                for so in SO_HIEU_RE.findall(s):
                    add("sua_doi", so, s)
        for a in doc.articles:
            if "sửa đổi" in a.ten.lower():
                for so in SO_HIEU_RE.findall(a.ten):
                    add("sua_doi", so, a.tieu_de)
                # "Sửa đổi, bổ sung khoản 1 Điều 5 của Luật Phòng, chống tham nhũng số 36/2018/QH14 …":
                # chỉ lấy số hiệu ĐẦU TIÊN của câu (các số sau thường là luật đã sửa trước đó)
                for para in a.noidung.split("\n"):
                    p = re.sub(r"^\d+\.\s*|^[a-zđ]\)\s*", "", para.strip())
                    if p.startswith(("Sửa đổi", "Bổ sung", "Thay thế")) and (m := SO_HIEU_RE.search(p)):
                        add("sua_doi", m.group(1), p)
    for a in doc.articles:
        text = f"{a.ten}. {a.noidung}"
        if "bãi bỏ" in a.ten.lower():
            for so in SO_HIEU_RE.findall(a.noidung):
                add("bai_bo", so, a.tieu_de)
        for s in _sentences(text):
            low = s.lower()
            if "hết hiệu lực" in low or "bãi bỏ" in low:
                for so in SO_HIEU_RE.findall(s):
                    add("het_hieu_luc" if "hết hiệu lực" in low else "bai_bo", so, s)


def _find_effective(doc: ParsedDoc) -> None:
    for a in doc.articles:
        if re.search(r"hiệu lực|thi hành", a.ten, re.IGNORECASE):
            if m := _HIEU_LUC.search(a.noidung):
                doc.hieu_luc_tu = _iso(m)
                return
    for a in reversed(doc.articles):
        if m := _HIEU_LUC.search(a.noidung):
            doc.hieu_luc_tu = _iso(m)
            return


def _validate(doc: ParsedDoc) -> None:
    if not doc.so_hieu:
        doc.errors.append("Không tìm thấy số hiệu văn bản — điền tay trước khi duyệt.")
    if not doc.articles:
        doc.errors.append("Không tách được điều nào.")
        return
    nums = [int(re.match(r"\d+", a.so).group()) for a in doc.articles]
    if nums[0] != 1:
        doc.warnings.append(f"Điều đầu tiên là Điều {nums[0]}, không phải Điều 1.")
    empty = [a.so for a in doc.articles if len(a.noidung) < 15]
    if empty:
        doc.warnings.append(f"Điều gần như trống: {', '.join(empty[:10])}.")
    odd = sum(1 for a in doc.articles for c in a.noidung if c == "�")
    if odd:
        doc.warnings.append(f"Có {odd} ký tự lỗi mã hoá — kiểm tra lại bản PDF.")


def parse_document(text: str, hints: Optional[dict] = None) -> ParsedDoc:
    """hints: metadata từ nguồn (vd. trang Công báo: ngày ban hành, hiệu lực) — dùng khi văn bản không ghi."""
    hints = hints or {}
    lines = clean_lines(text)
    doc = ParsedDoc()
    start = _parse_header(lines, doc)
    # Lời dẫn (Căn cứ…, "Quốc hội ban hành Luật sửa đổi…") = trước điều/chương đầu tiên
    pre = []
    for ln in lines[start:]:
        if _DIEU.match(ln) or _CHUONG.match(ln) or _PHAN.match(ln):
            break
        pre.append(ln)
    _parse_body(lines[start:], doc)
    _find_effective(doc)
    if not doc.ngay_ban_hanh:
        for ln in lines[-40:]:
            if "thông qua" in ln and (m := _DATE.search(ln)):
                doc.ngay_ban_hanh = _iso(m)
    doc.ngay_ban_hanh = doc.ngay_ban_hanh or hints.get("ngay_ban_hanh", "")
    doc.hieu_luc_tu = doc.hieu_luc_tu or hints.get("hieu_luc_tu", "")
    if not doc.so_hieu and hints.get("so_hieu"):
        doc.so_hieu = hints["so_hieu"]
    _find_relations(doc, " ".join(pre))
    _validate(doc)
    return doc
