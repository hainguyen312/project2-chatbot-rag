"""
Đặt tên cho đoạn chat bằng LLM (thay cho việc cắt 40 ký tự đầu của câu hỏi).

Tên tốt = cụm danh từ 3–8 từ nêu đúng VẤN ĐỀ PHÁP LÝ, viết hoa chữ đầu câu, không dấu
câu thừa, không "Câu hỏi về…". LLM lỗi / trả rác → dùng tên dự phòng từ câu hỏi.
"""
from __future__ import annotations

import os
import re
from typing import Any

TITLE_MODEL = os.getenv("CHAT_TITLE_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
MAX_TITLE_CHARS = 60
SHORT_QUESTION_WORDS = 5

_SYSTEM = (
    "Bạn đặt tên ngắn cho một cuộc trò chuyện hỏi đáp pháp luật Việt Nam.\n"
    "Yêu cầu:\n"
    "- 3 đến 8 từ tiếng Việt, nêu đúng VẤN ĐỀ pháp lý chính (vd. \"Lao động nữ mang thai bị sa thải\", "
    "\"Mức phạt xe đạp điện không đội mũ\", \"Chia di sản khi không có di chúc\").\n"
    "- Viết hoa chữ cái đầu, phần còn lại viết thường trừ tên riêng / tên văn bản.\n"
    "- KHÔNG mở đầu bằng \"Câu hỏi về\", \"Hỏi về\", \"Tư vấn\"; không dấu chấm, không ngoặc kép, không emoji.\n"
    "- Không đưa thông tin định danh (số CCCD, số điện thoại, tên người cụ thể).\n"
    "- Chỉ dùng thông tin có trong câu hỏi: KHÔNG thêm năm, con số, chi tiết mà câu hỏi không nêu.\n"
    "Chỉ in ra tên."
)

_BAD_PREFIX = re.compile(r"^(câu hỏi về|hỏi về|tư vấn về|tư vấn|tiêu đề\s*:|tên\s*:)\s*", re.IGNORECASE)


def fallback_title(question: str) -> str:
    """Cách cũ: 40 ký tự đầu của câu hỏi."""
    q = re.sub(r"\s+", " ", (question or "").strip())
    return (q[:40].rstrip() + "…") if len(q) > 40 else (q or "Cuộc trò chuyện mới")


def clean_title(raw: str) -> str:
    """Chuẩn hoá output LLM: bỏ ngoặc, tiền tố thừa, dấu câu cuối; cắt độ dài; viết hoa chữ đầu."""
    t = (raw or "").strip().splitlines()[0] if (raw or "").strip() else ""
    t = t.strip(" \t\"'“”‘’`*#-")
    t = _BAD_PREFIX.sub("", t)
    t = re.sub(r"[.。!?;:,]+$", "", t).strip()
    t = re.sub(r"\s+", " ", t)
    if len(t) > MAX_TITLE_CHARS:
        t = t[:MAX_TITLE_CHARS].rsplit(" ", 1)[0]
    return t[:1].upper() + t[1:] if t else ""


def generate_title(client: Any, question: str, answer: str = "") -> str:
    """Tên ngắn cho đoạn chat; không bao giờ raise (lỗi → fallback_title)."""
    question = (question or "").strip()
    if not question:
        return "Cuộc trò chuyện mới"
    if len(question.split()) <= SHORT_QUESTION_WORDS:
        # "nào", "bạn có thể làm gì"… — quá ngắn để LLM đặt tên mà không bịa chủ đề
        return clean_title(fallback_title(question)) or fallback_title(question)
    user = f"Câu hỏi đầu tiên:\n{question[:1500]}"
    if answer:
        user += f"\n\nTrích câu trả lời:\n{answer[:1200]}"
    try:
        resp = client.chat.completions.create(
            model=TITLE_MODEL,
            messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            temperature=0.2,
            max_tokens=40,
        )
        title = clean_title(resp.choices[0].message.content or "")
    except Exception as e:
        print(f"[Title] LLM lỗi, dùng tên dự phòng: {e}")
        return fallback_title(question)
    words = len(title.split())
    if not title or words < 2 or words > 14:
        return fallback_title(question)
    return title
