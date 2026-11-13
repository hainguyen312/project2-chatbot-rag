"""Rà soát văn bản người dùng tải lên (services/doc_review.py)."""
import io
import json
import zipfile

import pytest

from services import doc_review as dr
from tests.conftest import ScriptedChatClient, assistant

CONTRACT = (
    "HỢP ĐỒNG MUA BÁN HÀNG HOÁ\n"
    "Điều 1. Bên A bán cho Bên B 100 tấn thép, đơn giá 15.000.000 đồng/tấn.\n"
    "Điều 5. Bên vi phạm nghĩa vụ giao hàng chịu phạt 20% giá trị hợp đồng.\n"
) * 3

ART_301 = {"mapc": "M301", "ten": "Điều 42.1.LQ.301. Mức phạt vi phạm", "vbqppl": "Điều 301 Luật Thương mại 2005",
           "noidung": "Mức phạt không quá 8% giá trị phần nghĩa vụ hợp đồng bị vi phạm", "score": 0.8, "source": "phapdien"}
ART_418 = {"mapc": "M418", "ten": "Điều 14.1.LQ.418. Thoả thuận phạt vi phạm", "vbqppl": "Điều 418 Bộ luật Dân sự 2015",
           "noidung": "Mức phạt vi phạm do các bên thoả thuận", "score": 0.6, "source": "phapdien"}
WEB = {"mapc": "", "ten": "Bài báo", "noidung": "…", "source": "web"}


def _docx(paragraphs: list[str]) -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paragraphs)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def _analysis(risks=None):
    return json.dumps({
        "loai_van_ban": "Hợp đồng mua bán hàng hoá", "tieu_de": "HĐ mua bán thép",
        "cac_ben": [{"vai_tro": "Bên bán", "ten": "Công ty A"}],
        "tom_tat": "Bên A bán thép cho Bên B.",
        "y_chinh": [{"muc": "Giá", "noi_dung": "15 triệu đồng/tấn", "dieu_khoan": "Điều 1"}],
        "rui_ro": risks if risks is not None else [
            {"dieu_khoan": "Điều 5", "trich_dan": "phạt 20%", "van_de": "Vượt mức phạt tối đa",
             "muc_do": "cao", "truy_van": "mức phạt vi phạm hợp đồng thương mại"},
        ],
        "thieu_sot": ["Không có điều khoản giải quyết tranh chấp"],
    }, ensure_ascii=False)


def _run(client, search=lambda q: [ART_301, ART_418, WEB], text=CONTRACT):
    return list(dr.review_events(client, search, text, source={"type": "text"}))


# ── Lấy chữ ──────────────────────────────────────────────────────────────────
def test_docx_to_text():
    text, _ = dr.text_from_bytes(_docx(["Điều 1. Đối tượng", "Điều 2. Giá"]), "hd.docx")
    assert text.splitlines() == ["Điều 1. Đối tượng", "Điều 2. Giá"]


def test_html_to_text_skips_scripts_and_nav():
    page = "<html><nav>Menu</nav><script>x=1</script><p>Điều 1.</p><p>Điều 2.</p></html>"
    assert dr.html_to_text(page) == "Điều 1.\nĐiều 2."


def test_old_doc_rejected_with_hint():
    with pytest.raises(dr.ReviewError, match=r"\.docx"):
        dr.text_from_bytes(b"\xd0\xcf\x11\xe0", "hd.doc")


def test_plain_text_utf8():
    assert dr.text_from_bytes("Hợp đồng".encode(), "a.txt")[0] == "Hợp đồng"


def test_prepare_text_too_short():
    with pytest.raises(dr.ReviewError):
        dr.prepare_text("ngắn quá")


def test_prepare_text_truncates(monkeypatch):
    monkeypatch.setattr(dr, "MAX_DOC_CHARS", 500)
    text, truncated = dr.prepare_text(("Dòng nội dung hợp đồng.\n" * 100))
    assert truncated and len(text) <= 500


def test_google_docs_link_rewritten():
    assert dr._rewrite_link("https://docs.google.com/document/d/abc_123/edit") == \
        "https://docs.google.com/document/d/abc_123/export?format=txt"


@pytest.mark.parametrize("url", ["http://localhost/x", "http://127.0.0.1:8001/admin", "file:///etc/passwd"])
def test_fetch_link_blocks_internal(url):
    with pytest.raises(dr.ReviewError):
        dr.fetch_link(url)


# ── Luồng phân tích ──────────────────────────────────────────────────────────
Q301 = "Mức phạt không quá 8% giá trị phần nghĩa vụ hợp đồng bị vi phạm"


def _verify(*items):
    return json.dumps({"ket_luan": list(items)}, ensure_ascii=False)


def test_full_flow_grounded_on_retrieved_passages():
    verify = _verify({
        "id": 0, "danh_gia": "vi_pham", "muc_do": "cao",
        "can_cu": [{"mapc": "M_BIA", "trich": "bịa"},                 # không được tra → bỏ
                   {"mapc": "M301", "trich": Q301}],
        "giai_thich": "Phạt 20% vượt mức tối đa 8% [2].", "khuyen_nghi": "Giảm xuống ≤ 8% [2]",
    })
    client = ScriptedChatClient([assistant(_analysis()), assistant(verify)])
    events = _run(client)

    assert [e.get("stage") for e in events if e["type"] == "status"] == ["analyze", "search", "verify"]
    res = events[-1]
    assert res["type"] == "result" and res["tom_tat"] and res["thieu_sot"]
    # passages cùng khuôn với chat; điều được trích đứng đầu; không lẫn nguồn web
    assert [p["mapc"] for p in res["passages"]] == ["M301", "M418"]
    assert res["passages"][0]["vbqppl"] == "Điều 301 Luật Thương mại 2005"
    assert res["passages"][0]["ten"].startswith("Điều 42.1.LQ.301")
    [w] = res["canh_bao"]
    assert w["danh_gia"] == "vi_pham"
    assert w["can_cu"] == [{"p": 0, "trich": Q301}]
    assert w["giai_thich"] == "Phạt 20% vượt mức tối đa 8% [1]."     # [2] → [1] sau khi lọc
    assert w["khuyen_nghi"] == "Giảm xuống ≤ 8% [1]"
    assert w["tai_lieu"] == [0, 1] and w["ngoai_can_cu"] == []
    assert res["can_xem_lai"] == []

    assert "<van_ban>" in client.requests[0]["messages"][1]["content"]
    verify_payload = client.requests[1]["messages"][1]["content"]
    assert "Điều 301 Luật Thương mại" in verify_payload and "M301" not in verify_payload and "Bài báo" not in verify_payload
    assert client.requests[0]["response_format"] == {"type": "json_object"}


def test_fabricated_quote_is_dropped_but_citation_kept():
    verify = _verify({"id": 0, "danh_gia": "vi_pham", "giai_thich": "x [1]",
                      "can_cu": [{"mapc": "M301", "trich": "Mức phạt không được vượt quá 50% giá trị hợp đồng"}]})
    w = _run(ScriptedChatClient([assistant(_analysis()), assistant(verify)]))[-1]["canh_bao"][0]
    assert w["can_cu"] == [{"p": 0, "trich": ""}]


def test_warning_without_valid_basis_goes_to_review_list():
    verify = _verify({"id": 0, "danh_gia": "vi_pham", "can_cu": [{"mapc": "KHONG_CO", "trich": "…"}]})
    res = _run(ScriptedChatClient([assistant(_analysis()), assistant(verify)]))[-1]
    assert res["canh_bao"] == []
    [r] = res["can_xem_lai"]
    assert r["dieu_khoan"] == "Điều 5" and r["tai_lieu"] == [0, 1]


def test_insufficient_basis_verdict_goes_to_review_list():
    verify = _verify({"id": 0, "danh_gia": "chua_du_can_cu", "can_cu": []})
    res = _run(ScriptedChatClient([assistant(_analysis()), assistant(verify)]))[-1]
    assert res["canh_bao"] == [] and len(res["can_xem_lai"]) == 1


def test_mention_outside_basis_is_flagged():
    verify = _verify({"id": 0, "danh_gia": "rui_ro", "can_cu": [{"mapc": "M301", "trich": Q301}],
                      "giai_thich": "Theo Điều 301 Luật Thương mại [1], và Điều 98 Bộ luật Lao động 2019 quy định 150%."})
    w = _run(ScriptedChatClient([assistant(_analysis()), assistant(verify)]))[-1]["canh_bao"][0]
    assert w["ngoai_can_cu"] == ["Điều 98 Bộ luật Lao động 2019 quy định 150%"]


def test_two_queries_per_risk_are_merged():
    risks = [{"dieu_khoan": "Điều 4", "van_de": "làm thêm 100%", "muc_do": "cao",
              "truy_van": "tiền làm thêm giờ", "ten_dieu_luat": "Tiền lương làm thêm giờ"}]
    seen = []

    def search(q):
        seen.append(q)
        return [ART_418] if q == "tiền làm thêm giờ" else [{**ART_418, "score": 0.9}, ART_301]

    verify = _verify({"id": 0, "danh_gia": "chua_du_can_cu"})
    res = _run(ScriptedChatClient([assistant(_analysis(risks)), assistant(verify)]), search=search)[-1]
    assert sorted(seen) == ["Tiền lương làm thêm giờ", "tiền làm thêm giờ"]
    assert [p["mapc"] for p in res["passages"]] == ["M418", "M301"]
    assert res["passages"][0]["score"] == 0.9


def test_no_problem_verdict_is_dropped_and_sorted():
    risks = [
        {"dieu_khoan": "Điều 2", "van_de": "mơ hồ", "muc_do": "thap", "truy_van": "q1"},
        {"dieu_khoan": "Điều 5", "van_de": "phạt cao", "muc_do": "cao", "truy_van": "q2"},
        {"dieu_khoan": "Điều 7", "van_de": "ổn", "muc_do": "thap", "truy_van": "q3"},
    ]
    verify = _verify(
        {"id": 0, "danh_gia": "rui_ro", "muc_do": "thap", "can_cu": [{"mapc": "M418"}]},
        {"id": 1, "danh_gia": "vi_pham", "muc_do": "cao", "can_cu": [{"mapc": "M301"}]},
        {"id": 2, "danh_gia": "khong_van_de"},
    )
    res = _run(ScriptedChatClient([assistant(_analysis(risks)), assistant(verify)]))[-1]
    assert [w["dieu_khoan"] for w in res["canh_bao"]] == ["Điều 5", "Điều 2"]
    assert res["can_xem_lai"] == []


@pytest.mark.parametrize("quote, ok", [
    (Q301, True),
    ("“mức phạt  không quá 8% giá trị phần nghĩa vụ”", True),            # hoa/thường, ngoặc, khoảng trắng
    ("Mức phạt không quá 8% … nghĩa vụ hợp đồng bị vi phạm", True),       # có bỏ đoạn
    ("Mức phạt không quá 50% giá trị toàn bộ hợp đồng", False),
    ("ngắn", False),
])
def test_quote_in_source(quote, ok):
    assert dr.quote_in_source(quote, ART_301["noidung"]) is ok


def test_remap_markers():
    assert dr.remap_markers("A [1], B [2, 3] và C [3].", {2: 1, 3: 2}) == "A, B [1][2] và C [2]."
    assert dr.remap_markers("vi phạm, [1]. Và [1], [2].", {1: 1, 2: 2}) == "vi phạm [1]. Và [1][2]."


def test_no_risks_skips_search_and_verify():
    client = ScriptedChatClient([assistant(_analysis([]))])
    events = _run(client, search=lambda q: pytest.fail("không được tra cứu"))
    assert events[-1]["type"] == "result" and events[-1]["canh_bao"] == []
    assert len(client.requests) == 1


def test_search_failure_does_not_break_flow():
    def boom(q):
        raise RuntimeError("milvus down")
    verify = json.dumps({"ket_luan": [{"id": 0, "danh_gia": "vi_pham", "can_cu": [{"mapc": "M301"}]}]})
    res = _run(ScriptedChatClient([assistant(_analysis()), assistant(verify)]), search=boom)[-1]
    assert res["type"] == "result" and res["canh_bao"] == [] and res["can_xem_lai"][0]["tai_lieu"] == []


def test_invalid_llm_json_gives_user_error():
    events = _run(ScriptedChatClient([assistant("không phải json")]))
    assert events[-1]["type"] == "error"


def test_short_text_gives_user_error_without_llm_call():
    client = ScriptedChatClient([])
    events = _run(client, text="abc")
    assert events == [{"type": "error", "message": events[0]["message"]}] and not client.requests


def test_split_vbqppl():
    assert dr.split_vbqppl("(Điều 301 Luật số 36/2005/QH11, có hiệu lực thi hành kể từ ngày 01/01/2006)") == \
        ("Điều 301 Luật số 36/2005/QH11", "có hiệu lực thi hành kể từ ngày 01/01/2006")
    assert dr.split_vbqppl("Điều 8 Nghị định 100/2019/NĐ-CP") == ("Điều 8 Nghị định 100/2019/NĐ-CP", "")


# ── Nguồn web (như chat) ─────────────────────────────────────────────────────
WEB_HIGH = {"mapc": "", "ten": "Điều 301 Luật Thương mại: mức phạt vi phạm hợp đồng", "url": "https://thuvienphapluat.vn/a",
            "noidung": "Theo Điều 301 Luật Thương mại 2005, mức phạt đối với vi phạm nghĩa vụ hợp đồng không quá 8% giá trị phần nghĩa vụ bị vi phạm.",
            "source": "web", "trust_level": "high", "source_label": "[Web]", "score": 0.7}
WEB_MED = {**WEB_HIGH, "url": "https://blog-luat.vn/b", "trust_level": "medium", "source_label": "[Web - tham khảo]"}
WEB_SUMMARY = {**WEB_HIGH, "url": "", "ten": "Tổng hợp từ web"}       # câu Tavily tự viết, không phải nguồn
QW = "mức phạt đối với vi phạm nghĩa vụ hợp đồng không quá 8% giá trị phần nghĩa vụ bị vi phạm"


def _run_web(verify, web_hits, search=lambda q: [ART_301, ART_418], analysis=None):
    client = ScriptedChatClient([assistant(analysis or _analysis()), assistant(verify)])
    calls = []

    def web(q):
        calls.append(q)
        return web_hits

    events = list(dr.review_events(client, search, CONTRACT, source={"type": "text"}, web_search=web))
    return events, calls, client


def test_web_used_for_time_sensitive_risk_and_cited_like_chat():
    verify = _verify({"id": 0, "danh_gia": "vi_pham", "giai_thich": "Vượt 8% [1][2].",
                      "can_cu": [{"mapc": "M301", "trich": Q301}, {"mapc": dr._web_id(WEB_HIGH["url"]), "trich": QW}]})
    events, calls, client = _run_web(verify, [WEB_SUMMARY, WEB_HIGH])
    res = events[-1]
    assert calls == ["mức phạt vi phạm hợp đồng thương mại"]          # "phạt" → dễ đổi theo văn bản mới
    assert "Pháp Điển và web" in next(e["text"] for e in events if e.get("stage") == "search")
    web_p = [p for p in res["passages"] if p["source"] == "web"]
    assert [p["url"] for p in web_p] == ["https://thuvienphapluat.vn/a"]  # bỏ mục không có URL
    assert web_p[0]["trust_level"] == "high" and web_p[0]["source_label"] == "[Web]"
    w = res["canh_bao"][0]
    assert w["danh_gia"] == "vi_pham" and [c["trich"] for c in w["can_cu"]] == [Q301, QW]
    payload = client.requests[1]["messages"][1]["content"]
    assert '"nguon": "web"' in payload and '"do_tin_cay_nguon": "high"' in payload


def test_violation_only_on_medium_web_is_downgraded():
    verify = _verify({"id": 0, "danh_gia": "vi_pham", "can_cu": [{"mapc": dr._web_id(WEB_MED["url"]), "trich": QW}]})
    w = _run_web(verify, [WEB_MED], search=lambda q: [])[0][-1]["canh_bao"][0]
    assert w["danh_gia"] == "rui_ro"


def test_violation_on_high_web_alone_is_kept():
    verify = _verify({"id": 0, "danh_gia": "vi_pham", "can_cu": [{"mapc": dr._web_id(WEB_HIGH["url"]), "trich": QW}]})
    w = _run_web(verify, [WEB_HIGH], search=lambda q: [])[0][-1]["canh_bao"][0]
    assert w["danh_gia"] == "vi_pham"


def test_web_skipped_when_phapdien_strong_and_not_time_sensitive():
    risks = [{"dieu_khoan": "Điều 3", "van_de": "giao hàng mơ hồ", "muc_do": "thap", "truy_van": "địa điểm giao hàng"}]
    _, calls, _ = _run_web(_verify({"id": 0, "danh_gia": "khong_van_de"}), [WEB_HIGH],
                           search=lambda q: [{**ART_301, "score": 0.9}], analysis=_analysis(risks))
    assert calls == []


def test_web_mode_off(monkeypatch):
    monkeypatch.setattr(dr, "WEB_MODE", "off")
    _, calls, _ = _run_web(_verify({"id": 0, "danh_gia": "khong_van_de"}), [WEB_HIGH])
    assert calls == []


def test_article_named_in_web_source_is_not_flagged():
    verify = _verify({"id": 0, "danh_gia": "vi_pham", "giai_thich": "Theo Điều 301 Luật Thương mại 2005 [1].",
                      "can_cu": [{"mapc": dr._web_id(WEB_HIGH["url"]), "trich": QW}]})
    w = _run_web(verify, [WEB_HIGH], search=lambda q: [])[0][-1]["canh_bao"][0]
    assert w["ngoai_can_cu"] == []


def test_phapdien_article_promoted_before_web_source():
    """LLM chỉ dẫn web nêu 'Điều 301' trong khi Pháp Điển có Điều 301 → chèn Pháp Điển lên trước."""
    verify = _verify({"id": 0, "danh_gia": "vi_pham", "giai_thich": "Vượt mức 8% [1].",
                      "can_cu": [{"mapc": dr._web_id(WEB_HIGH["url"]), "trich": QW}]})
    res = _run_web(verify, [WEB_HIGH])[0][-1]
    w = res["canh_bao"][0]
    assert [res["passages"][c["p"]]["mapc"] for c in w["can_cu"]] == ["M301", dr._web_id(WEB_HIGH["url"])]
    assert w["giai_thich"] == "Vượt mức 8% [2]."          # marker vẫn trỏ đúng nguồn web



def test_candidates_use_short_codes_and_resolve_back():
    verify = _verify({"id": 0, "danh_gia": "vi_pham", "giai_thich": "x [1]", "can_cu": [{"ma": "n1", "trich": Q301}]})
    client = ScriptedChatClient([assistant(_analysis()), assistant(verify)])
    res = _run(client)[-1]
    payload = json.loads(client.requests[1]["messages"][1]["content"].split("\n\n", 1)[1])
    cands = payload["rui_ro"][0]["dieu_luat_ung_vien"]
    assert [c["ma"] for c in cands] == ["N1", "N2"] and "mapc" not in cands[0]
    assert res["passages"][res["canh_bao"][0]["can_cu"][0]["p"]]["mapc"] == "M301"
