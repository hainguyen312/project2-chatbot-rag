"""
Câu hỏi known-item tổng hợp cho evaluation/eval_retrieval_nogt.py (--synthetic N).

Mỗi câu = gpt-4o-mini đọc một điều ngẫu nhiên (seed cố định) và viết câu hỏi đời thường mà điều đó trả lời;
"gold" = chính điều đó. Đã cache ở evaluation/synthetic_queries.json (100 câu) → chạy lại không gọi API.
(Tách nguyên văn từ compare_title_vs_context.py, nay ở legacy/evaluation/.)
"""
from __future__ import annotations

import json
import os
import random

import pymysql
from dotenv import load_dotenv
from openai import OpenAI

BASE = os.path.dirname(os.path.abspath(__file__))
GEN_MODEL = "gpt-4o-mini"

load_dotenv(os.path.join(os.path.dirname(BASE), ".env"))
_client = None


def mysql():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", 3306)),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "123456789"),
        database=os.getenv("MYSQL_DATABASE", "law"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


SYNTH_PROMPT = (
    "Bạn là người dân không có chuyên môn pháp luật. Đọc điều luật dưới đây và viết MỘT câu hỏi "
    "thực tế mà người dân có thể hỏi, mà điều luật này trả lời được. Yêu cầu:\n"
    "- Mô tả tình huống/nhu cầu cụ thể, dùng ngôn ngữ đời thường.\n"
    "- KHÔNG chép lại tên điều, KHÔNG nhắc số hiệu điều/văn bản.\n"
    "- Nếu điều luật chỉ là định nghĩa/phạm vi/hiệu lực chung chung, trả về đúng chữ SKIP.\n"
    "Chỉ in ra câu hỏi."
)


def load_synthetic(n: int, seed: int = 42) -> list[dict]:
    path = os.path.join(BASE, "synthetic_queries.json")
    if os.path.exists(path):
        cached = json.load(open(path, encoding="utf-8"))
        if len(cached) >= n:
            return cached[:n]
    conn = mysql()
    with conn.cursor() as cur:
        cur.execute("SELECT mapc, ten, noidung FROM pddieu WHERE CHAR_LENGTH(noidung) > 200 ORDER BY mapc")
        rows = cur.fetchall()
    conn.close()
    rng = random.Random(seed)
    rng.shuffle(rows)

    out: list[dict] = []
    for r in rows:
        if len(out) >= n:
            break
        resp = _openai().chat.completions.create(
            model=GEN_MODEL,
            temperature=0.7,
            messages=[
                {"role": "system", "content": SYNTH_PROMPT},
                {"role": "user", "content": f"{r['ten']}\n{r['noidung'][:4000]}"},
            ],
        )
        q = resp.choices[0].message.content.strip()
        if q.upper().startswith("SKIP") or len(q) < 10:
            continue
        out.append({"query": q, "gold": [r["mapc"]], "source_title": r["ten"]})
        print(f"  synth {len(out)}/{n}: {q[:90]}")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return out


def _openai():
    global _client
    if _client is None:
        _client = OpenAI()
    return _client
