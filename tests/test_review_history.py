"""Lịch sử rà soát văn bản (services/review_history.py) — Mongo + file store giả."""
import json
from types import SimpleNamespace

import pytest

from services import doc_review as dr
from services.review_history import ReviewHistory
from tests.conftest import ScriptedChatClient, assistant


def _match(doc, q):
    return all(doc.get(k) == v for k, v in q.items())


class FakeCursor(list):
    def sort(self, key, direction):
        return FakeCursor(sorted(self, key=lambda d: d[key], reverse=direction < 0))

    def limit(self, n):
        return FakeCursor(self[:n])


class FakeCol:
    def __init__(self):
        self.docs: list[dict] = []
        self.fail_insert = False

    def insert_one(self, doc):
        if self.fail_insert:
            raise RuntimeError("mongo down")
        self.docs.append(dict(doc))

    def find(self, q, proj=None):
        out = []
        for d in self.docs:
            if _match(d, q):
                d = dict(d)
                for k, v in (proj or {}).items():
                    if v == 0:
                        d.pop(k, None)
                out.append(d)
        return FakeCursor(out)

    def find_one(self, q, proj=None):
        return next(iter(self.find(q)), None)

    def find_one_and_delete(self, q):
        d = self.find_one(q)
        if d:
            self.docs = [x for x in self.docs if x["_id"] != d["_id"]]
        return d

    def update_many(self, q, upd):
        n = 0
        for d in self.docs:
            if _match(d, q):
                d.update(upd["$set"]); n += 1
        return SimpleNamespace(modified_count=n)


class FakeFiles:
    def __init__(self):
        self.store: dict[int, bytes] = {}

    def put(self, data, *, filename, content_type):
        fid = len(self.store) + 1
        self.store[fid] = data
        return fid

    def get(self, fid):
        return self.store[fid]

    def delete(self, fid):
        self.store.pop(fid, None)


RESULT = {
    "type": "result",
    "document": {"type": "file", "name": "hd.docx", "chars": 900, "truncated": False},
    "loai_van_ban": "Hợp đồng lao động", "tieu_de": "HĐLĐ Sao Việt",
    "tom_tat": "…", "y_chinh": [], "thieu_sot": ["Thiếu bất khả kháng"],
    "canh_bao": [
        {"danh_gia": "vi_pham", "can_cu": [{"mapc": "M25", "vbqppl": "Điều 25 BLLĐ"}]},
        {"danh_gia": "rui_ro", "can_cu": []},
    ],
    "warnings": [], "disclaimer": "…",
}


@pytest.fixture
def h():
    return ReviewHistory(FakeCol(), FakeFiles())


def test_save_list_get_roundtrip(h):
    rid = h.save("demo_a", RESULT, text="nội dung", file_bytes=b"PK..", filename="hd.docx",
                 content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    [item] = h.list("demo_a")
    assert item["id"] == rid and item["title"] == "HĐLĐ Sao Việt"
    assert item["counts"] == {"vi_pham": 1, "canh_bao": 2, "thieu_sot": 1}
    assert item["file"]["name"] == "hd.docx" and item["file"]["size"] == 4
    assert "result" not in item and "text" not in item          # danh sách gọn

    full = h.get("demo_a", rid)
    assert full["text"] == "nội dung"
    assert full["result"]["canh_bao"][0]["can_cu"][0]["vbqppl"] == "Điều 25 BLLĐ"
    assert "type" not in full["result"]
    assert h.get_file("demo_a", rid)[:2] == (b"PK..", "hd.docx")


def test_other_user_cannot_read_or_delete(h):
    rid = h.save("demo_a", RESULT, file_bytes=b"x", filename="a.pdf")
    assert h.list("demo_b") == []
    assert h.get("demo_b", rid) is None
    assert h.get_file("demo_b", rid) is None
    assert h.delete("demo_b", rid) is False
    assert h.get("demo_a", rid) is not None


def test_delete_removes_file(h):
    rid = h.save("demo_a", RESULT, file_bytes=b"x", filename="a.pdf")
    assert h.delete("demo_a", rid) and h.files.store == {} and h.list("demo_a") == []


def test_no_file_for_text_source(h):
    rid = h.save("demo_a", {**RESULT, "document": {"type": "text"}, "tieu_de": ""}, text="…")
    assert h.get_file("demo_a", rid) is None
    assert h.list("demo_a")[0]["title"] == "Văn bản"


def test_failed_insert_does_not_orphan_file(h):
    h.col.fail_insert = True
    with pytest.raises(RuntimeError):
        h.save("demo_a", RESULT, file_bytes=b"x", filename="a.pdf")
    assert h.files.store == {}


def test_list_newest_first_and_retag_and_delete_all(h):
    a = h.save("demo_a", RESULT)
    b = h.save("demo_a", RESULT)
    assert [i["id"] for i in h.list("demo_a")] == [b, a]
    assert h.retag("demo_a", "uid1") == 2 and h.list("demo_a") == []
    assert h.delete_all("uid1") == 2 and h.list("uid1") == []


# ── Móc on_result trong doc_review ───────────────────────────────────────────
TEXT = "Điều 1. Bên A bán cho Bên B 100 tấn thép, đơn giá 15 triệu đồng/tấn.\n" * 5
NO_RISK = json.dumps({"loai_van_ban": "HĐ", "tieu_de": "T", "tom_tat": "x", "rui_ro": []})


def test_on_result_adds_history_id_and_receives_text():
    seen = {}

    def hook(result, text):
        seen.update(result=result, text=text)
        return {"history_id": "abc"}

    ev = list(dr.review_events(ScriptedChatClient([assistant(NO_RISK)]), lambda q: [], TEXT,
                               source={"type": "text"}, on_result=hook))[-1]
    assert ev["history_id"] == "abc"
    assert seen["text"].startswith("Điều 1.") and seen["result"]["tieu_de"] == "T"


def test_on_result_failure_keeps_result():
    def hook(result, text):
        raise RuntimeError("mongo down")

    ev = list(dr.review_events(ScriptedChatClient([assistant(NO_RISK)]), lambda q: [], TEXT,
                               source={"type": "text"}, on_result=hook))[-1]
    assert ev["type"] == "result" and ev["history_error"] and "history_id" not in ev


def test_content_type_guessed_from_name(h):
    rid = h.save("demo_a", RESULT, file_bytes=b"%PDF", filename="hd.pdf", content_type="application/octet-stream")
    assert h.get_file("demo_a", rid)[2] == "application/pdf"
