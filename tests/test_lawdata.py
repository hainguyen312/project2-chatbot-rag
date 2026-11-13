import pytest

from services.lawdata import store
from services.lawdata.parser import parse_document
from services.lawdata.sources import SourceError, parse_congbao_page

LUAT = """CÔNG BÁO/Số 518/Ngày 21-09-2026 53
QUỐC HỘI CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
Độc lập - Tự do - Hạnh phúc
Luật số: 26/2023/QH15
LUẬT
CĂN CƯỚC
Căn cứ Hiến pháp nước Cộng hòa xã hội chủ nghĩa Việt Nam;
Quốc hội ban hành Luật Căn cước.
Chương I
NHỮNG QUY ĐỊNH CHUNG
Điều 1. Phạm vi điều chỉnh
Luật này quy định về Cơ sở dữ liệu quốc gia về dân cư.
Điều 2. Giải thích từ
ngữ
Trong Luật này, các từ ngữ dưới đây được hiểu như sau:
1. Căn cước là thông tin cơ bản về nhân thân, lai lịch, nhân dạng
và sinh trắc học của một người.
2. Danh tính điện tử là thông tin của một người.
54
CÔNG BÁO/Số 518/Ngày 21-09-2026
Chương III
CẤP, QUẢN LÝ THẺ CĂN CƯỚC
Mục 1
THẺ CĂN CƯỚC
Điều 3. Độ tuổi cấp đổi thẻ căn cước
1. Công dân Việt Nam đã được cấp thẻ căn cước phải thực hiện thủ tục cấp đổi
thẻ căn cước khi đủ 14 tuổi, 25 tuổi, 40 tuổi và 60 tuổi.
a) Trường hợp thứ nhất;
b) Trường hợp thứ hai.
Điều 4. Hiệu lực thi hành
1. Luật này có hiệu lực thi hành từ ngày 01 tháng 7 năm 2024.
2. Luật Căn cước công dân số 59/2014/QH13 hết hiệu lực kể từ ngày Luật này có hiệu lực thi hành.
Luật này đã được Quốc hội khóa XV, kỳ họp thứ 6 thông qua ngày 27 tháng 11 năm 2023.
CHỦ TỊCH QUỐC HỘI
Vương Đình Huệ
"""

SUA_DOI = """QUỐC HỘI CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
Luật số: 15/2026/QH16
LUẬT
SỬA ĐỔI, BỔ SUNG MỘT SỐ ĐIỀU CỦA
LUẬT TRÁCH NHIỆM BỒI THƯỜNG CỦA NHÀ NƯỚC
Căn cứ Hiến pháp nước Cộng hòa xã hội chủ nghĩa Việt Nam;
Quốc hội ban hành Luật sửa đổi, bổ sung một số điều của Luật Trách nhiệm
bồi thường của Nhà nước số 10/2017/QH14.
Điều 1. Sửa đổi, bổ sung một số điều của Luật Trách nhiệm bồi thường
của Nhà nước
1. Sửa đổi, bổ sung Điều 3 như sau:
“Điều 3. Giải thích từ ngữ
1. Người thi hành công vụ là người được tuyển dụng.
Điều 4. Phạm vi trách nhiệm
Nội dung trích lại.”.
Điều 2. Điều khoản thi hành
Luật này có hiệu lực thi hành từ ngày 01 tháng 3 năm 2027.
"""

VBHN = """CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
VĂN BẢN HỢP NHẤT Độc lập - Tự do - Hạnh phúc
Số: 30/2026/VBHN-TT-BKHCN Hà Nội, ngày 06 tháng 11 năm 2026
THÔNG TƯ
Quy định chi tiết về lập dự toán
Thông tư số 39/2025/TT-BKHCN ngày 30 tháng 11 năm 2025 của Bộ trưởng, được sửa đổi, bổ sung bởi:
Thông tư số 55/2026/TT-BKHCN ngày 31 tháng 8 năm 2026.
Căn cứ Luật Ngân sách nhà nước số 89/2025/QH15;
Điều 1. Phạm vi điều chỉnh
Thông tư này quy định về lập dự toán.
Điều 2. Hiệu lực thi hành
Thông tư này có hiệu lực thi hành kể từ ngày 01 tháng 01 năm 2026.
"""


def test_parse_law_structure_and_metadata():
    d = parse_document(LUAT)
    assert (d.so_hieu, d.loai, d.trich_yeu, d.co_quan) == ("26/2023/QH15", "Luật", "Căn cước", "Quốc hội")
    assert d.ngay_ban_hanh == "2023-11-27" and d.hieu_luc_tu == "2024-07-01"
    assert [a.so for a in d.articles] == ["1", "2", "3", "4"]
    a2 = d.articles[1]
    assert a2.ten == "Giải thích từ ngữ"          # tên điều bị ngắt dòng được nối lại
    assert a2.chuong == "Chương I. NHỮNG QUY ĐỊNH CHUNG" and a2.muc == ""
    assert "nhân dạng và sinh trắc học" in a2.noidung   # dòng bị ngắt theo khổ giấy được nối
    assert "CÔNG BÁO" not in a2.noidung and "\n54" not in a2.noidung
    a3 = d.articles[2]
    assert a3.chuong == "Chương III. CẤP, QUẢN LÝ THẺ CĂN CƯỚC" and a3.muc == "Mục 1. THẺ CĂN CƯỚC"
    assert "a) Trường hợp thứ nhất;\nb) Trường hợp thứ hai." in a3.noidung
    assert "CHỦ TỊCH" not in d.articles[-1].noidung and "thông qua" not in d.articles[-1].noidung
    assert [(r.loai, r.so_hieu) for r in d.relations] == [("het_hieu_luc", "59/2014/QH13")]
    assert not d.errors and not d.warnings


def test_quoted_articles_of_amending_law_are_not_structure():
    d = parse_document(SUA_DOI)
    assert [a.so for a in d.articles] == ["1", "2"]
    assert d.articles[0].ten == "Sửa đổi, bổ sung một số điều của Luật Trách nhiệm bồi thường của Nhà nước"
    assert "“Điều 3. Giải thích từ ngữ" in d.articles[0].noidung
    assert ("sua_doi", "10/2017/QH14") in [(r.loai, r.so_hieu) for r in d.relations]
    assert all(not r.xac_nhan for r in d.relations)   # chỉ là gợi ý, người duyệt tích mới áp dụng


def test_consolidated_document_points_to_original():
    d = parse_document(VBHN)
    assert d.is_vbhn and d.so_hieu == "30/2026/VBHN-TT-BKHCN" and d.goc_so_hieu == "39/2025/TT-BKHCN"
    assert d.loai == "Thông tư" and d.ngay_ban_hanh == "2026-11-06"
    assert ("hop_nhat", "39/2025/TT-BKHCN") in [(r.loai, r.so_hieu) for r in d.relations]
    doc = d.to_dict()
    assert store.target_so_hieu(doc) == "39/2025/TT-BKHCN"
    assert store.build_vbqppl(doc, "1") == ("(Điều 1 Thông tư số 39/2025/TT-BKHCN theo văn bản hợp nhất số "
                                            "30/2026/VBHN-TT-BKHCN, có hiệu lực thi hành kể từ ngày 01/01/2026)")


def test_missing_number_and_hints():
    d = parse_document("THÔNG TƯ\nVề việc gì đó\nĐiều 1. Phạm vi\nNội dung đủ dài để không bị cảnh báo.",
                       {"hieu_luc_tu": "2026-12-20", "ngay_ban_hanh": "2026-11-02"})
    assert d.errors and "số hiệu" in d.errors[0]
    assert d.hieu_luc_tu == "2026-12-20" and d.ngay_ban_hanh == "2026-11-02"


def test_vbqppl_round_trips_through_citation_parser():
    doc = {"so_hieu": "26/2023/QH15", "loai": "Luật", "hieu_luc_tu": "2024-07-01"}
    v = store.build_vbqppl(doc, "21")
    assert v == "(Điều 21 Luật số 26/2023/QH15, có hiệu lực thi hành kể từ ngày 01/07/2024)"
    assert store.source_pair(v) == ("21", "26/2023/QH15")
    assert store.source_pair("(Điều 4 Thông tư số 256/2016/TT-BTC Quy định mức thu, có hiệu lực …)") == \
        ("4", "256/2016/TT-BTC")


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args):
        self.like = args[0]

    def fetchall(self):
        return [r for r in self.rows if self.like.strip("%") in r["vbqppl"]]


class FakeConn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return FakeCursor(self.rows)


def test_compute_diff_matches_existing_articles_by_number_and_document():
    rows = [
        {"mapc": "PD1", "ten": "Điều 1.1.1.1. Phạm vi", "noidung": "Nội dung  cũ\n", "vbqppl_link": None,
         "vbqppl": "(Điều 1 Luật số 26/2023/QH15, có hiệu lực …)"},
        {"mapc": "PD2", "ten": "Điều 1.1.1.2. Giải thích", "noidung": "Khác", "vbqppl_link": None,
         "vbqppl": "(Điều 2 Luật số 26/2023/QH15, có hiệu lực …)"},
        {"mapc": "PD9", "ten": "x", "noidung": "y", "vbqppl_link": None,
         "vbqppl": "(Điều 3 Nghị định số 70/2024/NĐ-CP, được hướng dẫn bởi Luật số 26/2023/QH15)"},
    ]
    doc = {"so_hieu": "26/2023/QH15", "articles": [
        {"so": "1", "noidung": "Nội dung cũ"}, {"so": "2", "noidung": "Mới"}, {"so": "3", "noidung": "Thêm"}]}
    diff = store.compute_diff(FakeConn(rows), doc)
    assert diff["counts"] == {"new": 1, "update": 1, "same": 1}
    acts = {i["so"]: (i["action"], i["mapc"]) for i in diff["items"]}
    assert acts == {"1": ("same", "PD1"), "2": ("update", "PD2"), "3": ("new", "VB:26/2023/QH15:D3")}


def test_parse_congbao_page():
    html = ('<title>Thông tư số 166/2026/TT-BTC bãi bỏ</title> Ban hành: 02/11/2026 - Hiệu lực: 20/12/2026 '
            '<a data-href="https://congbaocdn.chinhphu.vn/1/2026/11/12/470909_signed.pdf">x</a>')
    pdf, hints = parse_congbao_page(html, "https://congbao.chinhphu.vn/van-ban/x.htm")
    assert pdf.endswith("470909_signed.pdf")
    assert hints == {"ngay_ban_hanh": "2026-11-02", "hieu_luc_tu": "2026-12-20",
                     "tieu_de_trang": "Thông tư số 166/2026/TT-BTC bãi bỏ"}
    with pytest.raises(SourceError):
        parse_congbao_page("<html>không có file</html>", "https://congbao.chinhphu.vn/van-ban/x.htm")


def test_fetch_url_rejects_unsupported_and_internal_links(monkeypatch):
    from services.lawdata import sources

    with pytest.raises(SourceError, match="Chỉ hỗ trợ"):
        sources.fetch_url("https://example.com/trang-tin.html")
    with pytest.raises(SourceError, match="nội bộ"):
        sources.fetch_url("http://127.0.0.1/a.pdf")
