"""
Ví dụ cho mục "Đồ thị pháp luật: dẫn chiếu và kiểm tra ngoại lệ" của đồ án — lấy từ output THẬT của tool
vector_search (không gọi LLM, chỉ embedding truy vấn), bật / tắt kiểm tra ngoại lệ.

    python scripts/thesis_graph_examples.py   → evaluation/results/thesis_graph_examples.json
"""
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import services.agentic_rag as A  # noqa: E402
from retrieve.build_graph import GraphRAGRetriever, get_neo4j  # noqa: E402
from retrieve.lexical_bm25 import get_bm25  # noqa: E402
from retrieve.two_stage_search import client, collection  # noqa: E402

EXAMPLES = [
    {"id": "ket_hon", "query": "điều kiện kết hôn",
     "tinh_huong": "Người hỏi muốn kết hôn với con gái của chú ruột"},
    {"id": "sa_thai", "query": "tự ý bỏ việc 5 ngày bị sa thải",
     "tinh_huong": "Người sử dụng lao động muốn sa thải lao động nữ đang mang thai tự ý nghỉ việc"},
]


def run(retriever, driver, query, flag):
    A.AGENT_EXCEPTION_CHECK = flag
    content, hits = A.execute_agent_tool("vector_search", {"query": query, "top_k": 10}, retriever=retriever,
                                         neo4j_driver=driver, rerank_query=query)
    items = json.loads(content)
    return [{"hang": i + 1, "ma_phap_dien": it.get("ma_phap_dien"), "ten": it.get("ten"),
             "vbqppl": it.get("vbqppl"), "diem_lien_quan": it.get("diem_lien_quan"),
             "ngoai_le_cua": it.get("ngoai_le_cua"), "duoc_dan_chieu_tu": it.get("duoc_dan_chieu_tu"),
             "noidung": it.get("noidung") if (it.get("ngoai_le_cua") or it.get("duoc_dan_chieu_tu")) else None}
            for i, it in enumerate(items)]


def main():
    driver = get_neo4j()
    retriever = GraphRAGRetriever(driver, collection, client)
    get_bm25()
    out = {"ngay": date.today().isoformat(), "ghi_chu": "output tool vector_search (execute_agent_tool), top_k=10",
           "vi_du": []}
    for ex in EXAMPLES:
        out["vi_du"].append({**ex, "tat_kiem_tra_ngoai_le": run(retriever, driver, ex["query"], False),
                             "bat_kiem_tra_ngoai_le": run(retriever, driver, ex["query"], True)})
    A.AGENT_EXCEPTION_CHECK = True
    path = os.path.join(ROOT, "evaluation", "results", "thesis_graph_examples.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    for ex in out["vi_du"]:
        print(f"\n## {ex['query']}")
        for it in ex["bat_kiem_tra_ngoai_le"]:
            tag = " [ngoại lệ của " + it["ngoai_le_cua"] + "]" if it["ngoai_le_cua"] else (
                " [dẫn chiếu từ " + it["duoc_dan_chieu_tu"] + "]" if it["duoc_dan_chieu_tu"] else "")
            print(f"  {it['hang']:>2}. {it['ma_phap_dien']:<16} {(it['ten'] or '')[:55]}{tag}")
    print(f"\n✓ {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    main()
