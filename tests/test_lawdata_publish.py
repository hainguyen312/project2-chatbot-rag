"""Duyệt văn bản → cập nhật mọi chỉ mục (Milvus + Neo4j + BM25), lỗi thì 'Thử lại' làm nốt (services/lawdata)."""
import pytest

from services.lawdata import staging, store


@pytest.fixture
def env(monkeypatch):
    st = {"row": {"status": "publishing", "doc": {"so_hieu": "X"}, "publish_report": None},
          "calls": [], "fail": set(), "changed": ["M1", "M2"]}

    monkeypatch.setattr(staging, "get_row", lambda iid, cols="*": dict(st["row"]))
    monkeypatch.setattr(staging, "_diff", lambda doc: {"items": []})

    def _set(iid, where_status=None, **fields):
        st["row"].update(fields)
        return 1

    monkeypatch.setattr(staging, "_set", _set)
    monkeypatch.setattr(store, "publish_document", lambda item: {"changed_mapcs": list(st["changed"])})

    def reindex(mapcs, embed_title_only=False):
        st["calls"].append(("milvus", list(mapcs)))
        if "milvus" in st["fail"]:
            raise RuntimeError("milvus down")
        return {"embedded": len(mapcs)}

    def sync(mapcs, deleted=()):
        st["calls"].append(("neo4j", list(mapcs), list(deleted)))
        if "neo4j" in st["fail"]:
            raise RuntimeError("neo4j down")
        return {"graph_dieu": len(mapcs)}

    monkeypatch.setattr(store, "reindex_mapcs", reindex)
    monkeypatch.setattr(store, "sync_graph", sync)
    monkeypatch.setattr(store, "rebuild_bm25_async", lambda: st["calls"].append(("bm25",)))
    return st


def test_publish_updates_all_indexes(env):
    staging.publish("i1")
    assert env["calls"] == [("milvus", ["M1", "M2"]), ("neo4j", ["M1", "M2"], []), ("bm25",)]
    assert env["row"]["status"] == "published"
    assert env["row"]["publish_report"]["graph_dieu"] == 2


def test_graph_error_marks_retryable(env):
    env["fail"].add("neo4j")
    staging.publish("i1")
    assert env["row"]["status"] == "publish_error"
    assert "Neo4j: neo4j down" in env["row"]["error"]
    assert env["row"]["publish_report"]["pending_index"] == ["M1", "M2"]


def test_retry_reindexes_pending_even_if_diff_empty(env):
    env["fail"].add("milvus")
    staging.publish("i1")
    # Lần thử lại: MySQL đã có dữ liệu → diff không còn điều nào đổi, nhưng vẫn phải làm nốt chỉ mục
    env["fail"].clear()
    env["calls"].clear()
    env["changed"] = []
    env["row"]["status"] = "publishing"
    staging.publish("i1")
    assert ("neo4j", ["M1", "M2"], []) in env["calls"] and ("milvus", ["M1", "M2"]) in env["calls"]
    assert env["row"]["status"] == "published" and "pending_index" not in env["row"]["publish_report"]
