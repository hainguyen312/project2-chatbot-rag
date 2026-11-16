"""
Sinh các bảng LaTeX cho quyển đồ án từ file kết quả trong repo — số liệu chỉ lấy từ file, không gõ tay.

    python scripts/make_thesis_tables.py --refresh-snapshot   # truy vấn DB + pytest + đếm mã → snapshot JSON
    python scripts/make_thesis_tables.py                      # chỉ sinh bảng từ JSON đã có

Nguồn (ghi trong comment đầu mỗi file .tex sinh ra):
  evaluation/results/nogt_eval_*.json        truy xuất (pooled LLM-judge + known-item)
  legacy/evaluation/results/results.json     đợt 1 (5 câu, có đáp án)
  evaluation/results/tool_eval_*.json        gọi công cụ
  evaluation/results/memory_eval_*.json      bộ nhớ
  evaluation/results/exception_eval_*.json   kiểm tra ngoại lệ
  evaluation/results/thesis_snapshot.json    số liệu dữ liệu (MySQL / Neo4j / Milvus), kiểm thử, mã nguồn
Đầu ra: SOICT_.../tables/*.tex (mỗi file một môi trường table hoàn chỉnh, \\input từ chương).
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import statistics
import subprocess
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "evaluation", "results")
THESIS = os.path.join(ROOT, "SOICT_DATN_NguyenDucHai_20241592E_GVHD_VuVanThieu")
OUT = os.path.join(THESIS, "tables")
SNAPSHOT = os.path.join(RES, "thesis_snapshot.json")

RUN_MAIN = "nogt_eval_20261116_115034.json"   # các pipeline thử nghiệm (w_vec = 1.0)
RUN_SWEEP = "nogt_eval_20261116_121754.json"  # dò trọng số vector (hybrid_wv*)
TOOL_41 = "tool_eval_gpt-4.1_20261116_144452.json"


# ------------------------------------------------------------------ tiện ích
def vn(x: float, d: int = 3) -> str:
    """Số thập phân kiểu Việt Nam: 0,588."""
    return f"{x:.{d}f}".replace(".", "{,}")


def pct(x: float, d: int = 1) -> str:
    return f"{x * 100:.{d}f}".replace(".", "{,}") + r"\%"


def thousands(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def load(name: str, base: str = RES) -> dict:
    with open(os.path.join(base, name), encoding="utf-8") as f:
        return json.load(f)


def latest(pattern: str) -> str:
    files = sorted(glob.glob(os.path.join(RES, pattern)))
    if not files:
        raise SystemExit(f"Không có file {pattern}")
    return os.path.basename(files[-1])


def sign_p(w: int, l: int) -> float:
    """Kiểm định dấu hai phía (nhị thức chính xác, p = 0,5)."""
    n = w + l
    if n == 0:
        return 1.0
    k = min(w, l)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def fmt_p(p: float) -> str:
    return r"$<$0{,}001" if p < 0.001 else vn(p, 3)


def write(name: str, sources: list[str], body: str) -> None:
    os.makedirs(OUT, exist_ok=True)
    head = "% Tự sinh bởi scripts/make_thesis_tables.py — KHÔNG sửa tay.\n% Nguồn: " + "; ".join(sources) + "\n"
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        f.write(head + body.strip() + "\n")
    print(f"  ✓ tables/{name}  ← {', '.join(sources)}")


def table(caption: str, label: str, spec: str, header: list[str], rows: list[list[str]], note: str = "",
          sep: str = "") -> str:
    lines = [r"\begin{table}[H]", r"\centering", r"\small", rf"\caption{{{caption}}}", rf"\label{{{label}}}",
             *([rf"\setlength{{\tabcolsep}}{{{sep}}}"] if sep else []),
             rf"\begin{{tabular}}{{{spec}}}", r"\hline", " & ".join(rf"\textbf{{{h}}}" for h in header) + r" \\ \hline"]
    lines += [" & ".join(r) + r" \\ \hline" for r in rows]
    lines.append(r"\end{tabular}")
    if note:
        lines.append(rf"\par\smallskip\parbox{{0.95\linewidth}}{{\footnotesize {note}}}")
    lines.append(r"\end{table}")
    return "\n".join(lines)


# ------------------------------------------------------------------ snapshot DB / test / mã nguồn
def refresh_snapshot() -> None:
    sys.path.insert(0, ROOT)
    import pymysql

    snap: dict = {"ngay": date.today().isoformat()}
    c = pymysql.connect(host=os.getenv("MYSQL_HOST", "localhost"), user=os.getenv("MYSQL_USER", "root"),
                        password=os.getenv("MYSQL_PASSWORD", "123456789"), database=os.getenv("MYSQL_DATABASE", "law"),
                        charset="utf8mb4")
    with c.cursor() as cur:
        for k, sql in {
            "chude": "SELECT COUNT(*) FROM pdchude", "demuc": "SELECT COUNT(*) FROM pddemuc",
            "chuong": "SELECT COUNT(*) FROM pdchuong", "dieu": "SELECT COUNT(*) FROM pddieu",
            "cap_lien_quan": "SELECT COUNT(*) FROM pdmuclienquan", "file": "SELECT COUNT(*) FROM pdfile",
            "bang": "SELECT COUNT(*) FROM pdtable",
            "noidung_tb": "SELECT ROUND(AVG(CHAR_LENGTH(noidung))) FROM pddieu",
            "noidung_max": "SELECT MAX(CHAR_LENGTH(noidung)) FROM pddieu",
            "dieu_gt_4000": "SELECT SUM(CHAR_LENGTH(noidung) > 4000) FROM pddieu",
            "dieu_gt_6000": "SELECT SUM(CHAR_LENGTH(noidung) > 6000) FROM pddieu",
            "vbqppl_co_ghi_chu_sua_doi": "SELECT COUNT(*) FROM pddieu WHERE vbqppl LIKE '%% bởi %%'",
        }.items():
            cur.execute(sql)
            snap[k] = int(cur.fetchone()[0] or 0)
    c.close()

    from retrieve.build_graph import get_neo4j

    d = get_neo4j()
    with d.session() as s:
        for k, q in {
            "neo4j_dieu": "MATCH (n:Dieu) RETURN count(n) AS c",
            "neo4j_khoan": "MATCH (n:Khoan) RETURN count(n) AS c",
            "neo4j_khoan_ngoai_le": "MATCH (n:Khoan) WHERE n.ngoai_le RETURN count(n) AS c",
            "neo4j_dieu_ngoai_le": "MATCH (n:Dieu) WHERE n.ngoai_le RETURN count(n) AS c",
            "neo4j_vanban": "MATCH (n:VanBan) RETURN count(n) AS c",
            "neo4j_tham_chieu": "MATCH ()-[r:THAM_CHIEU]->() RETURN count(r) AS c",
            "neo4j_tham_chieu_ngoai_le": "MATCH ()-[r:THAM_CHIEU]->() WHERE r.ngoai_le RETURN count(r) AS c",
            "neo4j_lien_quan": "MATCH ()-[r:LIEN_QUAN]->() RETURN count(r) AS c",
            "neo4j_thuoc": "MATCH ()-[r:THUOC]->() RETURN count(r) AS c",
            "neo4j_duoc_sua_doi_boi": "MATCH ()-[r:DUOC_SUA_DOI_BOI]->() RETURN count(r) AS c",
        }.items():
            snap[k] = int(s.run(q).single()["c"])
    d.close()

    from pymilvus import Collection, connections

    connections.connect("default", host=os.getenv("MILVUS_HOST", "localhost"), port=os.getenv("MILVUS_PORT", "19530"))
    snap["milvus_tendieu"] = int(Collection("phapdien_simple_tendieu").num_entities)
    snap["milvus_ctx_chunks"] = int(Collection("phapdien_ctx_chunks").num_entities)

    from retrieve.lexical_bm25 import get_bm25

    idx = get_bm25()
    snap["bm25_tai_lieu"] = len(idx.mapcs)

    # Kiểm thử đơn vị
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "tests"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    per: dict[str, int] = {}
    for line in out.splitlines():
        if "::" in line:
            per[line.split("::")[0].replace("tests/", "")] = per.get(line.split("::")[0].replace("tests/", ""), 0) + 1
    snap["kiem_thu"] = dict(sorted(per.items(), key=lambda x: -x[1]))
    res = subprocess.run([sys.executable, "-m", "pytest", "tests"], cwd=ROOT, capture_output=True, text=True).stdout
    m = re.search(r"(\d+) passed", res)
    snap["kiem_thu_dat"] = int(m.group(1)) if m else 0
    snap["kiem_thu_tong"] = sum(per.values())

    # Mã nguồn (không tính legacy/)
    def count(globs: list[str]) -> tuple[int, int]:
        files = sorted({f for g in globs for f in glob.glob(os.path.join(ROOT, g), recursive=True)
                        if "/legacy/" not in f and "node_modules" not in f})
        return len(files), sum(sum(1 for _ in open(f, encoding="utf-8", errors="ignore")) for f in files)

    snap["ma_nguon"] = {
        "may_chu": count(["rag_api.py", "services/**/*.py", "agents/**/*.py", "retrieve/**/*.py",
                          "data_processing/**/*.py"]),
        "giao_dien": count(["frontend/src/**/*.ts", "frontend/src/**/*.tsx"]),
        "kiem_thu": count(["tests/**/*.py"]),
        "danh_gia": count(["evaluation/**/*.py"]),
        "thu_thap": count(["law-crawler/**/*.py"]),
        "uml": count(["docs/uml/**/*.puml"]),
    }
    with open(SNAPSHOT, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    print(f"  ✓ {os.path.relpath(SNAPSHOT, ROOT)}")


# ------------------------------------------------------------------ bảng
NAMES = {
    "old": "Ban đầu: vector tên điều, xếp hạng cũ",
    "vector_new": "Vector tên điều, xếp hạng lại",
    "ctx_only": "Vector đoạn nội dung",
    "bm25_only": "Chỉ BM25",
    "hybrid_norerank": "Lai, không xếp hạng lại",
    "hybrid_new": r"Lai, xếp hạng lại ($w_{v}=1{,}0$)",
    "hybrid_wv05": r"\textbf{Lai, xếp hạng lại ($w_{v}=0{,}5$, cấu hình cuối)}",
    "hybrid_graph": "Lai + cộng điểm đồ thị",
    "hybrid_closure": "Lai + kèm điều được dẫn chiếu",
}


def tab_truyxuat() -> None:
    main, sweep = load(RUN_MAIN), load(RUN_SWEEP)
    order = ["old", "vector_new", "ctx_only", "bm25_only", "hybrid_norerank", "hybrid_new", "hybrid_wv05",
             "hybrid_graph", "hybrid_closure"]
    rows = []
    for n in order:
        run = sweep if n == "hybrid_wv05" else main
        s, k = run["summary"][n], run["known_item"][n]
        rows.append([NAMES[n], vn(s["P@5"]), vn(s["P@10"]), vn(s["MRR@10(rel=2)"]), vn(s["Top1"], 2),
                     vn(k["Hit@1"], 2), vn(k["Hit@10"], 2), vn(k["MRR@10"])])
    write("tab_truyxuat.tex", [RUN_MAIN, RUN_SWEEP + " (dòng w=0,5)"], table(
        "Kết quả truy xuất: chấm mù bằng LLM trên 50 câu hỏi thật và đánh giá theo mục đã biết (KI) trên 100 câu",
        "tab:kq_truyxuat", "|L{4.4cm}|c|c|c|c|c|c|c|",
        ["Cấu hình", "P@5", "P@10", "MRR", "Top1", r"\shortstack{KI\\Hit@1}", r"\shortstack{KI\\Hit@10}",
         r"\shortstack{KI\\MRR}"], rows,
        "MRR tính với điều có mức liên quan 2 đầu tiên trong 10 kết quả. Không đưa nDCG vào bảng vì nDCG "
        "chuẩn hoá theo tập kết quả gộp của từng lần chạy nên không so được giữa hai lần chạy.", sep="4pt"))


def tab_trongso() -> None:
    sw = load(RUN_SWEEP)
    order = [("hybrid_new", "1,0"), ("hybrid_wv07", "0,7"), ("hybrid_wv05", "0,5"), ("hybrid_wv03", "0,3"),
             ("hybrid_wv0", "0"), ("bm25_only", "chỉ BM25")]
    pq = sw["per_query"]
    qs = list(pq["hybrid_new"])
    rows = []
    for n, w in order:
        s, k = sw["summary"][n], sw["known_item"][n]
        if n == "hybrid_new":
            cmp = "--"
        else:
            def wl(m):
                a = sum(pq[n][q][m] > pq["hybrid_new"][q][m] + 1e-9 for q in qs)
                b = sum(pq[n][q][m] < pq["hybrid_new"][q][m] - 1e-9 for q in qs)
                return a, b
            w1, l1 = wl("nDCG@10")
            cmp = f"{w1}/{l1} ($p={fmt_p(sign_p(w1, l1))}$)"
        rows.append([w, vn(s["P@5"]), vn(s["P@10"]), vn(s["nDCG@10"]), vn(s["MRR@10(rel=2)"]),
                     vn(k["Hit@1"], 2), vn(k["Hit@10"], 2), cmp])
    write("tab_trongso.tex", [RUN_SWEEP], table(
        "Dò trọng số vector trong RRF (cùng một lần chạy, 50 câu hỏi thật và 100 câu mục đã biết)",
        "tab:trongso", "|L{1.4cm}|c|c|c|c|c|c|L{2.9cm}|",
        ["$w_{v}$", "P@5", "P@10", "nDCG@10", "MRR", r"\shortstack{KI\\Hit@1}", r"\shortstack{KI\\Hit@10}",
         r"Thắng/thua so với $w_{v}=1{,}0$ (nDCG)"],
        rows, "nDCG so được giữa các dòng vì cùng một tập kết quả gộp. Kiểm định dấu hai phía trên 50 câu.", sep="4pt"))


def tab_sign_old() -> None:
    sw = load(RUN_SWEEP)
    pq, qs = sw["per_query"], list(sw["per_query"]["old"])
    rows = []
    for n, label in [("hybrid_norerank", NAMES["hybrid_norerank"]), ("hybrid_new", NAMES["hybrid_new"]),
                     ("hybrid_wv05", r"Lai, xếp hạng lại ($w_{v}=0{,}5$)"), ("bm25_only", NAMES["bm25_only"])]:
        w = sum(pq[n][q]["nDCG@10"] > pq["old"][q]["nDCG@10"] + 1e-9 for q in qs)
        l = sum(pq[n][q]["nDCG@10"] < pq["old"][q]["nDCG@10"] - 1e-9 for q in qs)
        rows.append([label, str(w), str(len(qs) - w - l), str(l), fmt_p(sign_p(w, l))])
    write("tab_sign_old.tex", [RUN_SWEEP], table(
        "So sánh từng câu hỏi theo nDCG@10 với cấu hình ban đầu (50 câu, kiểm định dấu hai phía)",
        "tab:pl_sign", "|L{6.4cm}|c|c|c|c|", ["Cấu hình", "Thắng", "Hoà", "Thua", "$p$"], rows))


def tab_sign_hybrid() -> None:
    """Các biến thể so với hybrid_new (w=1,0) trong CÙNG lần chạy chính — nDCG@10 so được trong một lần chạy."""
    mn = load(RUN_MAIN)
    pq, qs = mn["per_query"], list(mn["per_query"]["hybrid_new"])
    rows = []
    for n, label in [("hybrid3_new", "Lai ba nguồn (thêm vector đoạn nội dung)"), ("hybrid_graph", NAMES["hybrid_graph"]),
                     ("hybrid_closure", NAMES["hybrid_closure"]), ("bm25_only", NAMES["bm25_only"])]:
        w = sum(pq[n][q]["nDCG@10"] > pq["hybrid_new"][q]["nDCG@10"] + 1e-9 for q in qs)
        l = sum(pq[n][q]["nDCG@10"] < pq["hybrid_new"][q]["nDCG@10"] - 1e-9 for q in qs)
        rows.append([label, vn(mn["summary"][n]["nDCG@10"]), str(w), str(len(qs) - w - l), str(l), fmt_p(sign_p(w, l))])
    write("tab_sign_hybrid.tex", [RUN_MAIN], table(
        r"So sánh từng câu hỏi theo nDCG@10 với cấu hình lai, xếp hạng lại ($w_{v}=1{,}0$; nDCG@10 $=$ "
        + vn(mn["summary"]["hybrid_new"]["nDCG@10"]) + ") trong cùng lần chạy",
        "tab:pl_sign_hybrid", "|L{6.0cm}|c|c|c|c|c|", ["Cấu hình", "nDCG@10", "Thắng", "Hoà", "Thua", "$p$"], rows,
        "Kiểm định dấu hai phía trên 50 câu."))


def tab_dot1() -> None:
    d = load("results.json", os.path.join(ROOT, "legacy", "evaluation", "results"))
    names = {"semantic_search": "Vector nội dung", "hybrid_search": "Vector + độ trùng từ ($\\alpha=0{,}6$)",
             "two_stage_search": "Vector tên điều + xếp hạng theo độ trùng từ",
             "hybrid_rerank": "Vector tên điều + LLM chấm lại"}
    rows = []
    for k, label in names.items():
        m = d[k]["k=10"]["average_metrics"]["retrieval_metrics"]
        rows.append([label, vn(m["precision@k"]), vn(m["hit@k"], 2), vn(m["ndcg@k"]), vn(m["mrr@k"])])
    n = d["semantic_search"]["k=10"]["num_queries"]
    write("tab_dot1.tex", ["legacy/evaluation/results/results.json"], table(
        f"Thử nghiệm ban đầu trên {n} câu hỏi có đáp án gán tay (10 kết quả đầu)", "tab:dot1",
        "|L{6.6cm}|c|c|c|c|", ["Chiến lược", "P@10", "Hit@10", "nDCG@10", "MRR"], rows,
        f"Chỉ {n} câu hỏi nên kết quả chỉ mang tính định hướng."))


def tab_toolcall() -> str:
    mini = latest("tool_eval_gpt-4o-mini_*.json")
    a, b = load(TOOL_41)["summary"], load(mini)["summary"]
    def row(label, key, f):
        return [label, f(a[key]), f(b[key])]
    rows = [
        ["Tình huống đạt mọi kỳ vọng", f"{pct(a['case_pass'])} ({round(a['case_pass'] * a['cases'])}/{a['cases']})",
         f"{pct(b['case_pass'])} ({round(b['case_pass'] * b['cases'])}/{b['cases']})"],
        row("Gọi đủ công cụ bắt buộc", "must_call_recall", pct),
        row("Gọi công cụ nên gọi", "should_call_recall", pct),
        row("Gọi công cụ bị cấm", "forbidden_rate", pct),
        row("Công cụ đầu tiên đúng kỳ vọng", "first_tool_acc", pct),
        row("Luật buộc web kích hoạt đúng", "forced_web_acc", pct),
        row("Mã điều bịa đặt", "invalid_id_rate", pct),
        row("Chạm trần vòng lặp", "max_iter_rate", pct),
        row("Câu trả lời chứa từ khoá kỳ vọng", "answer_check", pct),
        row("Số lượt gọi công cụ trung bình", "avg_calls", lambda x: vn(x, 2)),
        row("Thời gian trung bình mỗi tình huống (giây)", "avg_latency_s", lambda x: vn(x, 1)),
        row("Tổng chi phí (USD)", "cost_usd", lambda x: vn(x, 2)),
    ]
    write("tab_toolcall.tex", [TOOL_41, mini], table(
        "Đánh giá gọi công cụ trên 23 tình huống (cùng mã nguồn, hai mô hình tác tử)", "tab:pl_tool",
        "|L{7.4cm}|L{2.9cm}|L{2.9cm}|", ["Chỉ số", "GPT-4.1", "GPT-4o-mini"], rows))
    return mini


def tab_memory() -> list[str]:
    files = sorted(os.path.basename(f) for f in glob.glob(os.path.join(RES, "memory_eval_*.json")))
    rows = []
    for f in files:
        s = load(f)["summary"]
        lat = [r.get("latency_s") for r in load(f)["rows"] if r.get("latency_s") is not None]
        act = s["actions"]
        rows.append([f.replace("memory_eval_", "").replace(".json", "").replace("_", " "),
                     f"{round(s['case_pass'] * s['cases'])}/{s['cases']}", str(s["sensitive_leaks"]),
                     str(s["duplicate_or_stale"]),
                     f"{act.get('ADD', 0)}/{act.get('UPDATE', 0)}/{act.get('DELETE', 0)}/{act.get('NOOP', 0)}",
                     vn(statistics.median(lat), 2) if lat else "--"])
    write("tab_memory.tex", files, table(
        "Các lần chạy đánh giá bộ nhớ dài hạn (14 kịch bản)", "tab:pl_mem_runs", "|L{2.4cm}|c|L{1.7cm}|L{1.9cm}|L{2.5cm}|L{2.1cm}|",
        ["Lần chạy", "Đạt", "Lộ nhạy cảm", "Trùng/lỗi thời", "Thêm / sửa / xoá / bỏ qua", "Trung vị độ trễ (giây)"], rows))
    return files


def tab_ngoaile() -> str:
    f = latest("exception_eval_*.json")
    rows_ = load(f)["retrieval"]["rows"]
    n = len(rows_)
    a_on = [r for r in rows_ if r["a_on"]]
    a_off = [r for r in rows_ if r["a_off"]]
    def ratio(xs, key):
        return sum(r[key] for r in xs), len(xs)
    rows = [
        ["Điều chính (A) có trong kết quả", f"{sum(r['a_off'] for r in rows_)}/{n}", f"{sum(r['a_on'] for r in rows_)}/{n}"],
        ["Điều ngoại lệ (B) có trong kết quả", f"{sum(r['b_off'] for r in rows_)}/{n}", f"{sum(r['b_on'] for r in rows_)}/{n}"],
        ["B có trong kết quả, xét các case A có mặt", "{}/{}".format(*ratio(a_off, "b_off")),
         "{}/{}".format(*ratio(a_on, "b_on"))],
        ["Số kết quả trả về trung bình", vn(statistics.mean(r["n_off"] for r in rows_), 1),
         vn(statistics.mean(r["n_on"] for r in rows_), 1)],
    ]
    write("tab_ngoaile.tex", [f, "evaluation/exception_cases.json"], table(
        f"Kiểm tra ngoại lệ ở mức truy xuất ({n} cặp điều chính và điều ngoại lệ sinh từ đồ thị)",
        "tab:ngoaile", "|L{7.2cm}|c|c|", ["Chỉ số", "Tắt kiểm tra", "Bật kiểm tra"], rows))
    return f


def tab_snapshot() -> None:
    s = load("thesis_snapshot.json")
    src = ["evaluation/results/thesis_snapshot.json (truy vấn MySQL, Neo4j, Milvus ngày " + s["ngay"] + ")"]
    rows = [
        ["Chủ đề / đề mục / chương", f"{s['chude']} / {s['demuc']} / {thousands(s['chuong'])}", "MySQL"],
        ["Điều luật", thousands(s["dieu"]), "MySQL"],
        ["Cặp điều liên quan", thousands(s["cap_lien_quan"]), "MySQL (nguồn của LIEN\\_QUAN)"],
        ["File đính kèm / bảng biểu", f"{thousands(s['file'])} / {thousands(s['bang'])}", "MySQL"],
        ["Độ dài nội dung điều (trung bình / lớn nhất)",
         f"{thousands(s['noidung_tb'])} / {thousands(s['noidung_max'])} ký tự", "--"],
        ["Điều dài hơn 4.000 / 6.000 ký tự", f"{thousands(s['dieu_gt_4000'])} / {thousands(s['dieu_gt_6000'])}", "--"],
        ["Vector tên điều", thousands(s["milvus_tendieu"]), "Milvus"],
        ["Tài liệu BM25 (sau khi tách điều dài theo khoản)", thousands(s["bm25_tai_lieu"]), "Chỉ mục BM25"],
    ]
    write("tab_dulieu.tex", src, table("Thống kê dữ liệu", "tab:dulieu", "|L{6.6cm}|L{3.4cm}|L{3.6cm}|",
                                        ["Dữ liệu", "Số lượng", "Nơi lưu"], rows))
    rows = [
        ["Dieu (điều luật)", thousands(s["neo4j_dieu"]), "kèm vbqppl, số hiệu, số điều"],
        ["Khoan (khoản của điều có từ hai khoản)", thousands(s["neo4j_khoan"]), ""],
        ["Khoản ngoại lệ", thousands(s["neo4j_khoan_ngoai_le"]), "khoản bắt đầu bằng ``Không được'', ``Cấm''\\ldots"],
        ["Điều ngoại lệ", thousands(s["neo4j_dieu_ngoai_le"]), "tiêu đề hoặc có khoản ngoại lệ"],
        ["VanBan (văn bản)", thousands(s["neo4j_vanban"]), "phân tích từ vbqppl"],
        ["Cạnh THAM\\_CHIEU", thousands(s["neo4j_tham_chieu"]), "phân tích dẫn chiếu trong nội dung"],
        ["\\quad trong ngữ cảnh ngoại lệ", thousands(s["neo4j_tham_chieu_ngoai_le"]), "``trừ trường hợp'', ``không thuộc''\\ldots"],
        ["Cạnh LIEN\\_QUAN (có hướng)", thousands(s["neo4j_lien_quan"]), "hai chiều từ pdmuclienquan"],
        ["Cạnh THUOC / DUOC\\_SUA\\_DOI\\_BOI", f"{thousands(s['neo4j_thuoc'])} / {thousands(s['neo4j_duoc_sua_doi_boi'])}", "từ vbqppl"],
    ]
    write("tab_dothi.tex", src, table("Thống kê đồ thị tri thức (Neo4j)", "tab:dothi", "|L{5.6cm}|L{2.0cm}|L{6.0cm}|",
                                       ["Thành phần", "Số lượng", "Ghi chú"], rows))
    kt = s["kiem_thu"]
    rows = [[f"\\texttt{{{f.replace('_', chr(92) + '_')}}}", str(n)] for f, n in kt.items()]
    rows.append([r"\textbf{Tổng}", rf"\textbf{{{s['kiem_thu_tong']}}}"])
    write("tab_kiemthu.tex", ["evaluation/results/thesis_snapshot.json (pytest --collect-only, ngày " + s["ngay"] + ")"],
          table(f"Tổng kết kiểm thử đơn vị ({s['kiem_thu_dat']}/{s['kiem_thu_tong']} đạt)", "tab:tong_kiemthu",
                "|L{8.4cm}|c|", ["Tệp kiểm thử", "Số ca"], rows))
    m = s["ma_nguon"]
    rows = [["Máy chủ dịch vụ (Python)", str(m["may_chu"][0]), thousands(m["may_chu"][1])],
            ["Giao diện (TypeScript, TSX)", str(m["giao_dien"][0]), thousands(m["giao_dien"][1])],
            ["Kiểm thử đơn vị (Python)", str(m["kiem_thu"][0]), thousands(m["kiem_thu"][1])],
            ["Bộ đánh giá (Python)", str(m["danh_gia"][0]), thousands(m["danh_gia"][1])],
            ["Công cụ thu thập dữ liệu (Python)", str(m["thu_thap"][0]), thousands(m["thu_thap"][1])],
            ["Biểu đồ UML (PlantUML)", str(m["uml"][0]), "--"]]
    write("tab_thongke.tex", ["evaluation/results/thesis_snapshot.json (đếm file, không tính legacy/, ngày " + s["ngay"] + ")"],
          table("Thống kê mã nguồn", "tab:thongke", "|L{6.4cm}|c|c|", ["Thành phần", "Số file", "Số dòng"], rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-snapshot", action="store_true")
    args = ap.parse_args()
    if args.refresh_snapshot:
        refresh_snapshot()
    tab_truyxuat()
    tab_trongso()
    tab_sign_old()
    tab_sign_hybrid()
    tab_dot1()
    tab_toolcall()
    tab_memory()
    tab_ngoaile()
    tab_snapshot()


if __name__ == "__main__":
    main()
