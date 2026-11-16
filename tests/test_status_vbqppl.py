"""Hiệu lực suy từ ghi chú vbqppl của Pháp Điển (services/lawdata/status.py)."""
from services.lawdata import status

AMENDED = ("(Điều 3 Nghị định số 112/2014/NĐ-CP, có hiệu lực thi hành kể từ ngày 15/01/2015, có nội dung được "
           "sửa đổi, có nội dung được bổ sung bởi Điều 1 Nghị định số 34/2023/NĐ-CP có hiệu lực thi hành kể từ "
           "ngày 31/07/2023)")
REPEALED = ("(Điều 4 Thông tư số 25/2016/TT-BLĐTBXH, có hiệu lực thi hành kể từ ngày 09/09/2016, bị bãi bỏ bởi "
            "Điều 2 Thông tư số 5/2021/TT-BLĐTBXH có hiệu lực thi hành kể từ ngày 01/08/2021)")
PLAIN = "(Điều 8 Luật số 52/2014/QH13, có hiệu lực thi hành kể từ ngày 01/01/2015)"


def hit(mapc, vbqppl, score):
    return {"mapc": mapc, "vbqppl": vbqppl, "score": score, "source": "phapdien"}


def test_amended_note():
    n = status.vbqppl_notes([hit("A", AMENDED, 1)])["A"]
    assert n["trang_thai"] == "con_hieu_luc"
    assert "sửa đổi, bổ sung bởi Điều 1 Nghị định số 34/2023/NĐ-CP (hiệu lực từ 31/07/2023)" in n["ghi_chu"]


def test_repealed_article_penalized_and_sorted_last():
    out = status.annotate_hits([hit("R", REPEALED, 0.9), hit("P", PLAIN, 0.5)])
    assert [h["mapc"] for h in out] == ["P", "R"]
    assert out[1]["hieu_luc"]["trang_thai"] == "het_hieu_luc"
    assert out[1]["score"] == 0.9 * status.HET_HIEU_LUC_PENALTY
    assert "hieu_luc" not in out[0]


def test_ledger_overrides_vbqppl(monkeypatch):
    monkeypatch.setattr(status, "status_notes",
                        lambda hits: {"A": {"trang_thai": "het_hieu_luc", "ghi_chu": "Theo sổ cái admin."}})
    out = status.annotate_hits([hit("A", AMENDED, 1.0)])
    assert out[0]["hieu_luc"]["ghi_chu"] == "Theo sổ cái admin."


def test_ledger_error_still_uses_vbqppl(monkeypatch):
    def boom(hits):
        raise RuntimeError("MySQL down")

    monkeypatch.setattr(status, "status_notes", boom)
    assert status.annotate_hits([hit("R", REPEALED, 1.0)])[0]["hieu_luc"]["trang_thai"] == "het_hieu_luc"
