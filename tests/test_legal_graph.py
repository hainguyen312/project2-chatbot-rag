"""Đồ thị pháp luật (retrieve/legal_graph.py): phân tích vbqppl, dẫn chiếu, khoản, đồng bộ từng phần."""
import pytest

import retrieve.legal_graph as LG
from retrieve.legal_graph import (
    build_lookup, extract_references, parse_vbqppl, reference_edges, reference_keys,
    resolve_reference, select_relevant_text, split_khoan,
)


# ── vbqppl ───────────────────────────────────────────────────────────────────
class TestParseVbqppl:
    def test_plain(self):
        p = parse_vbqppl("(Điều 8 Luật số 52/2014/QH13, có hiệu lực thi hành kể từ ngày 01/01/2015)")
        assert (p["so_dieu"], p["loai"], p["so_hieu"], p["hieu_luc_tu"]) == ("8", "Luật", "52/2014/QH13", "2015-01-01")
        assert p["hanh_vi"] == [] and p["sua_doi_boi"] == []

    def test_long_so_hieu(self):
        p = parse_vbqppl("(Điều 6 Thông tư liên tịch số 01/2020/TTLT-VKSNDTC-TANDTC-BCA-BQP-BNN&PTNT-BTC-BTP, "
                         "có hiệu lực thi hành kể từ ngày 15/03/2020)")
        assert p["loai"] == "Thông tư liên tịch"
        assert p["so_hieu"] == "01/2020/TTLT-VKSNDTC-TANDTC-BCA-BQP-BNN&PTNT-BTC-BTP"

    def test_amended(self):
        p = parse_vbqppl("(Điều 3 Nghị định số 112/2014/NĐ-CP, có hiệu lực thi hành kể từ ngày 15/01/2015, có nội "
                         "dung được sửa đổi, có nội dung được bổ sung bởi Điều 1 Nghị định số 34/2023/NĐ-CP có hiệu "
                         "lực thi hành kể từ ngày 31/07/2023)")
        assert p["hanh_vi"] == ["sua_doi", "bo_sung"]
        assert p["sua_doi_boi"] == [{"dieu": ["1"], "loai": "Nghị định", "so_hieu": "34/2023/NĐ-CP",
                                     "hieu_luc_tu": "2023-07-31"}]

    def test_multiple_amending_docs(self):
        p = parse_vbqppl("(Điều 2 Luật số 10/2012/QH13, có hiệu lực thi hành kể từ ngày 01/05/2013, có nội dung được "
                         "sửa đổi bởi Điều 1, Điều 3 Luật số 20/2015/QH13 có hiệu lực thi hành kể từ ngày 01/01/2016; "
                         "Điều 5 Nghị định số 7/2020/NĐ-CP có hiệu lực thi hành kể từ ngày 01/02/2020)")
        assert [(a["dieu"], a["so_hieu"]) for a in p["sua_doi_boi"]] == [(["1", "3"], "20/2015/QH13"),
                                                                        (["5"], "7/2020/NĐ-CP")]

    @pytest.mark.parametrize("phrase, act", [("bị bãi bỏ", "bai_bo"), ("có nội dung bị bãi bỏ", "bai_bo_mot_phan")])
    def test_repeal_whole_vs_partial(self, phrase, act):
        p = parse_vbqppl(f"(Điều 4 Thông tư số 25/2016/TT-BLĐTBXH, có hiệu lực thi hành kể từ ngày 09/09/2016, "
                         f"{phrase} bởi Điều 2 Thông tư số 5/2021/TT-BLĐTBXH có hiệu lực thi hành kể từ ngày 01/08/2021)")
        assert p["hanh_vi"] == [act]

    def test_admin_format_vbhn(self):
        # services/lawdata/store.build_vbqppl
        p = parse_vbqppl("(Điều 5 Nghị định số 100/2019/NĐ-CP theo văn bản hợp nhất số 15/VBHN-BGTVT, "
                         "có hiệu lực thi hành kể từ ngày 01/01/2020)")
        assert (p["so_dieu"], p["so_hieu"]) == ("5", "100/2019/NĐ-CP")

    @pytest.mark.parametrize("s", ["", None, "QUY ĐỊNH MỨC THU PHÍ THĂM QUAN"])
    def test_unparseable(self, s):
        assert parse_vbqppl(s) is None


# ── Dẫn chiếu ────────────────────────────────────────────────────────────────
def refs(text):
    return [(r["so_dieu"], r["khoan"], r["diem"], r["pham_vi"], r["so_hieu"]) for r in extract_references(text)]


class TestExtractReferences:
    def test_internal_with_point_list(self):
        assert refs("không thuộc các trường hợp cấm kết hôn theo quy định tại các điểm a, b, c và d khoản 2 "
                    "Điều 5 của Luật này.") == [("5", ["2"], ["a", "b", "c", "d"], "noi_bo", None)]

    def test_points_and_clauses(self):
        assert refs("theo quy định tại điểm a, điểm b khoản 1 và khoản 2 Điều 6 Thông tư này.") == [
            ("6", ["1", "2"], ["a", "b"], "noi_bo", None)]

    def test_external_with_number(self):
        assert refs("quy định tại khoản 1, 2 và 3 Điều 7 Nghị định số 100/2019/NĐ-CP ngày 30/12/2019") == [
            ("7", ["1", "2", "3"], [], "ngoai", "100/2019/NĐ-CP")]

    def test_list_inherits_document(self):
        # "Điều 11" không ghi văn bản nhưng nằm trong danh sách kết thúc bằng Nghị định số 22/2019
        out = refs("thực hiện theo Điều 42 Luật Tố cáo và Điều 11, Điều 12 Nghị định số 22/2019/NĐ-CP.")
        assert out == [("42", [], [], "ten", None),
                       ("11", [], [], "ngoai", "22/2019/NĐ-CP"),
                       ("12", [], [], "ngoai", "22/2019/NĐ-CP")]

    def test_name_with_number(self):
        assert refs("theo Điều 8 Luật Hôn nhân và gia đình số 52/2014/QH13") == [("8", [], [], "ngoai", "52/2014/QH13")]

    def test_unqualified(self):
        assert refs("trừ trường hợp quy định tại Điều 9.") == [("9", [], [], "khong_ro", None)]

    @pytest.mark.parametrize("text", ["“Điều 5. Phạm vi điều chỉnh", "Sửa đổi như sau:\nĐiều 5. Phạm vi"])
    def test_quoted_article_heading_ignored(self, text):
        assert refs(text) == []

    def test_constitution_not_resolved_as_internal(self):
        assert refs("Điều 12 Hiến pháp năm 2013")[0][3] == "ten"

    def test_this_article_ignored(self):
        assert refs("quy định tại khoản 1 Điều này") == []


class TestResolve:
    LOOKUP = {("52/2014/QH13", "5"): "M5", ("22/2019/NĐ-CP", "11"): "N11"}

    @pytest.mark.parametrize("ref, src, expect", [
        ({"pham_vi": "noi_bo", "so_dieu": "5", "so_hieu": None}, "52/2014/QH13", "M5"),
        ({"pham_vi": "khong_ro", "so_dieu": "5", "so_hieu": None}, "52/2014/QH13", "M5"),
        ({"pham_vi": "ngoai", "so_dieu": "11", "so_hieu": "22/2019/NĐ-CP"}, "52/2014/QH13", "N11"),
        ({"pham_vi": "ten", "so_dieu": "5", "so_hieu": None}, "52/2014/QH13", None),
        ({"pham_vi": "noi_bo", "so_dieu": "5", "so_hieu": None}, None, None),
        ({"pham_vi": "noi_bo", "so_dieu": "99", "so_hieu": None}, "52/2014/QH13", None),
    ])
    def test_resolve(self, ref, src, expect):
        assert resolve_reference(ref, src, self.LOOKUP) == expect


ROWS = [
    {"mapc": "M5", "vbqppl": "(Điều 5 Luật số 52/2014/QH13, có hiệu lực thi hành kể từ ngày 01/01/2015)",
     "noidung": "1. Bảo vệ chế độ hôn nhân.\n2. Cấm các hành vi sau đây:\na) Kết hôn giả tạo;"},
    {"mapc": "M8", "vbqppl": "(Điều 8 Luật số 52/2014/QH13, có hiệu lực thi hành kể từ ngày 01/01/2015)",
     "noidung": "1. Nam, nữ kết hôn phải: d) không thuộc trường hợp tại các điểm a, b khoản 2 Điều 5 của Luật này;"
                " theo Điều 8 của Luật này; xem Điều 5 Luật này."},
    {"mapc": "T1", "vbqppl": "(Điều 1 Thông tư số 1/2016/TT-BTP, có hiệu lực thi hành kể từ ngày 01/07/2016)",
     "noidung": "Hướng dẫn Điều 8 Luật số 52/2014/QH13 và Điều 99 Luật số 52/2014/QH13."},
]


class TestEdges:
    def test_lookup(self):
        assert build_lookup(ROWS) == {("52/2014/QH13", "5"): "M5", ("52/2014/QH13", "8"): "M8",
                                      ("1/2016/TT-BTP", "1"): "T1"}

    def test_edges_merged_no_self_loop_unresolved_dropped(self):
        edges = {(e["src"], e["tgt"]): e for e in reference_edges(ROWS, build_lookup(ROWS))}
        assert set(edges) == {("M8", "M5"), ("T1", "M8")}       # M8→M8 (tự dẫn) và Điều 99 (không có) bị bỏ
        assert edges[("M8", "M5")]["khoan"] == ["2"]              # 2 lần dẫn chiếu tới M5 gộp một cạnh
        assert edges[("M8", "M5")]["diem"] == ["a", "b"]
        assert edges[("T1", "M8")]["pham_vi"] == "ngoai"

    def test_reference_keys_for_graph_lookup(self):
        assert reference_keys([ROWS[2]]) == {("52/2014/QH13", "8"), ("52/2014/QH13", "99")}


# ── Khoản ────────────────────────────────────────────────────────────────────
class TestSplitKhoan:
    def test_preamble_and_clauses(self):
        assert split_khoan("Mở đầu.\n1. Một\n2. Hai\na) điểm") == [("0", "Mở đầu."), ("1", "1. Một"),
                                                                   ("2", "2. Hai\na) điểm")]

    def test_out_of_order_numbers_stay_inside_clause(self):
        # "1." lặp lại bên trong khoản 2 (danh sách con) không phải khoản mới
        parts = split_khoan("1. A\n2. B gồm:\n1. b-một\n3. C")
        assert [s for s, _ in parts] == ["1", "2", "3"] and "b-một" in parts[1][1]

    def test_no_clauses(self):
        assert split_khoan("Một đoạn duy nhất.") == [("0", "Một đoạn duy nhất.")]
        assert split_khoan("") == []


class TestSelectRelevantText:
    LONG = "\n".join(
        [f"{i}. Phạt tiền đối với hành vi vi phạm quy định số {i} về biển báo đường bộ " + "x " * 300
         for i in range(1, 9)]
        + ["9. Phạt tiền từ 4.000.000 đồng đối với người điều khiển xe không chấp hành hiệu lệnh đèn tín hiệu "
           "giao thông " + "y " * 100]
    )

    def test_short_text_untouched(self):
        assert select_relevant_text("1. A\n2. B", "đèn", 4000) == ("1. A\n2. B", False)

    def test_keeps_matching_clause_from_the_end(self):
        out, truncated = select_relevant_text(self.LONG, "không chấp hành đèn tín hiệu giao thông", 2500)
        assert truncated and len(out) <= 2500
        assert "9. Phạt tiền từ 4.000.000" in out            # khoản cuối — cắt phần đầu sẽ mất
        assert "[…]" in out and "get_full_article" in out

    def test_original_order_preserved(self):
        out, _ = select_relevant_text(self.LONG, "đèn tín hiệu biển báo", 4000)
        nums = [int(ln.split(".")[0]) for ln in out.splitlines() if ln[:1].isdigit()]
        assert nums == sorted(nums)

    def test_no_query_falls_back_to_head(self):
        out, truncated = select_relevant_text(self.LONG, "", 1000)
        assert truncated and out.startswith("1. Phạt tiền") and out.endswith("[đã rút gọn]")

    def test_no_clause_structure_falls_back_to_head(self):
        out, _ = select_relevant_text("z " * 3000, "đèn", 500)
        assert out.endswith("[đã rút gọn]") and len(out) <= 500


class TestPayloads:
    def test_khoan_only_for_multi_clause_articles(self):
        p = LG.khoan_payload([ROWS[0], {"mapc": "S", "noidung": "Một đoạn."}])
        assert [k["id"] for k in p] == ["M5#1", "M5#2"] and p[1]["noidung"].startswith("2. Cấm")

    def test_vanban_payload(self):
        row = {"mapc": "A", "vbqppl": "(Điều 3 Nghị định số 112/2014/NĐ-CP, có hiệu lực thi hành kể từ ngày "
                                      "15/01/2015, có nội dung được sửa đổi bởi Điều 1 Nghị định số 34/2023/NĐ-CP "
                                      "có hiệu lực thi hành kể từ ngày 31/07/2023)"}
        thuoc, sua = LG.vanban_payload([row, {"mapc": "B", "vbqppl": None}])
        assert thuoc == [{"mapc": "A", "so_hieu": "112/2014/NĐ-CP", "loai": "Nghị định",
                          "hieu_luc_tu": "2015-01-15", "so_dieu": "3"}]
        assert sua[0]["so_hieu"] == "34/2023/NĐ-CP" and sua[0]["goc"] == "112/2014/NĐ-CP"


# ── Đồng bộ từng phần ────────────────────────────────────────────────────────
class TestSyncMapcs:
    @pytest.fixture
    def calls(self, monkeypatch):
        log = []
        monkeypatch.setattr(LG, "article_rows", lambda conn, mapcs: [r for r in ROWS if r["mapc"] in mapcs])
        for name in ("ensure_schema", "write_admin_relations"):
            monkeypatch.setattr(LG, name, lambda *a, _n=name, **k: log.append((_n,)))
        monkeypatch.setattr(LG, "write_hierarchy", lambda d, c, rows: log.append(("hierarchy", len(rows))))
        monkeypatch.setattr(LG, "write_dieu", lambda d, rows: log.append(("dieu", [r["mapc"] for r in rows])))
        monkeypatch.setattr(LG, "write_khoan", lambda d, rows, fresh=False: log.append(("khoan", fresh)) or 2)
        monkeypatch.setattr(LG, "write_vanban", lambda d, rows, fresh=False: log.append(("vanban", fresh)))
        monkeypatch.setattr(LG, "lookup_from_graph",
                            lambda d, keys: {k: v for k, v in build_lookup(ROWS).items() if k in keys})
        monkeypatch.setattr(LG, "write_thamchieu",
                            lambda d, edges, sources=None: log.append(("tham_chieu", edges, sources)) or len(edges))
        monkeypatch.setattr(LG, "delete_dieu", lambda d, m: log.append(("delete", list(m))))
        return log

    def test_order_and_incremental_flags(self, calls):
        out = LG.sync_mapcs(object(), object(), ["M8", "M8"], deleted=["VB:old"])
        names = [c[0] for c in calls]
        assert names.index("dieu") < names.index("khoan") < names.index("tham_chieu")
        assert ("khoan", False) in calls and ("vanban", False) in calls      # xoá dữ liệu cũ rồi ghi lại
        tc = next(c for c in calls if c[0] == "tham_chieu")
        assert [(e["src"], e["tgt"]) for e in tc[1]] == [("M8", "M5")] and tc[2] == ["M8"]
        assert calls[-1] == ("delete", ["VB:old"])
        assert out == {"graph_dieu": 1, "graph_khoan": 2, "graph_tham_chieu": 1, "graph_deleted": 1}

    def test_nothing_to_do(self, calls):
        assert LG.sync_mapcs(object(), object(), [])["graph_dieu"] == 0 and calls == []


# ── Ngoại lệ / cấm ───────────────────────────────────────────────────────────
class TestExceptionMarkers:
    @pytest.mark.parametrize("clause, expect", [
        ("2. Cấm các hành vi sau đây:", True),
        ("4. Không được xử lý kỷ luật lao động đối với người lao động đang nghỉ ốm", True),
        ("Trừ trường hợp quy định tại khoản 2", True),
        ("1. Trường hợp không có hợp đồng thì…", False),     # câu điều kiện, không phải cấm
        ("3. Cấp giấy phép trong 5 ngày", False),
        ("2. Cầm cố tài sản", False),
    ])
    def test_clause(self, clause, expect):
        assert LG.is_exception_clause(clause) is expect

    def test_title_and_article(self):
        assert LG.is_exception_title("Điều 20.2.LQ.37. Trường hợp người sử dụng lao động không được thực hiện quyền")
        assert not LG.is_exception_title("Điều 8.4.LQ.8. Điều kiện kết hôn")
        assert LG.is_exception_article("Điều 8.4.LQ.5. Bảo vệ chế độ hôn nhân", "1. Bảo vệ.\n2. Cấm các hành vi sau đây:")
        assert not LG.is_exception_article("Điều 8.4.LQ.8. Điều kiện kết hôn", "1. Nam từ đủ 20 tuổi\n2. Nhà nước")

    def test_reference_context_flag(self):
        out = extract_references("1. Người lao động ốm đau, trừ trường hợp quy định tại điểm b khoản 1 Điều 36 của "
                                 "Bộ luật này. 2. Thực hiện theo Điều 9 của Bộ luật này.")
        assert [(r["so_dieu"], r["ngoai_le"]) for r in out] == [("36", True), ("9", False)]

    def test_edge_flag_merged(self):
        rows = [ROWS[0], {"mapc": "M8", "vbqppl": ROWS[1]["vbqppl"],
                          "noidung": "Theo Điều 5 Luật này. Không thuộc trường hợp cấm tại khoản 2 Điều 5 của Luật này."}]
        (e,) = reference_edges(rows, build_lookup(rows))
        assert e["ngoai_le"] is True and e["khoan"] == ["2"]

    def test_khoan_payload_flags(self):
        p = LG.khoan_payload([ROWS[0]])
        assert [k["ngoai_le"] for k in p] == [False, True]

    def test_selection_keeps_exception_clause_without_word_overlap(self):
        body = "\n".join([f"{i}. Hình thức sa thải áp dụng khi tự ý bỏ việc trường hợp {i} " + "x " * 300
                          for i in range(1, 6)]
                         + ["6. Không được xử lý kỷ luật đối với lao động nữ mang thai, nghỉ ốm đau."])
        out, _ = select_relevant_text(body, "tự ý bỏ việc bị sa thải", 2500)
        assert "6. Không được xử lý kỷ luật" in out
