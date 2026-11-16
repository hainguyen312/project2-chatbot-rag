"""
Agentic RAG: OpenAI tool-calling loop (vector + graph + web + full article).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

# Giới hạn nội dung đưa vào prompt tool (UI bucket giữ full)
LLM_MAX_NOIDUNG_CHARS = int(os.getenv("AGENT_LLM_MAX_NOIDUNG_CHARS", "4000"))
LLM_MAX_PASSAGE_CHARS = int(os.getenv("AGENT_LLM_MAX_PASSAGE_CHARS", "4000"))
LLM_MAX_WEB_CHARS = int(os.getenv("AGENT_LLM_MAX_WEB_CHARS", "1500"))
# Rerank: 1 = sau overlap gọi LLM chấm lại top-N (tốn API); 0 = chỉ overlap + Milvus order
RAG_AGENT_LLM_RERANK = os.getenv("RAG_AGENT_LLM_RERANK", "0").strip().lower() in ("1", "true", "yes")
AGENT_VECTOR_POOL_MULT = max(1, int(os.getenv("AGENT_VECTOR_POOL_MULT", "3")))
# Đồ thị (Neo4j): tự kèm điều/khoản được dẫn chiếu bởi top kết quả vector_search
AGENT_REF_CLOSURE = os.getenv("AGENT_REF_CLOSURE", "1").strip().lower() in ("1", "true", "yes")
AGENT_REF_CLOSURE_SEEDS = int(os.getenv("AGENT_REF_CLOSURE_SEEDS", "3"))
AGENT_REF_CLOSURE_MAX = int(os.getenv("AGENT_REF_CLOSURE_MAX", "4"))
AGENT_REF_CLOSURE_CHARS = int(os.getenv("AGENT_REF_CLOSURE_CHARS", "1500"))
# Đồ thị: tự kèm điều/khoản CẤM / NGOẠI LỆ nối với top kết quả ("điều chính cho phép, điều liên quan cấm")
AGENT_EXCEPTION_CHECK = os.getenv("AGENT_EXCEPTION_CHECK", "1").strip().lower() in ("1", "true", "yes")
AGENT_EXCEPTION_SEEDS = int(os.getenv("AGENT_EXCEPTION_SEEDS", "5"))
AGENT_EXCEPTION_MAX = int(os.getenv("AGENT_EXCEPTION_MAX", "3"))
# Cộng điểm cho ứng viên liên kết với ứng viên khác (THAM_CHIEU / LIEN_QUAN); 0 = tắt
GRAPH_BOOST_BETA = float(os.getenv("GRAPH_BOOST_BETA", "0"))

from retrieve.legal_graph import parse_vbqppl, select_relevant_text
from services.lawdata.status import annotate_hits, resolve_aliases

_MYSQL_CFG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "123456789"),
    "database": os.getenv("MYSQL_DATABASE", "law"),
    "charset": "utf8mb4",
}

_DIEU_CONTEXT_CYPHER = """
MATCH (d:Dieu) WHERE d.mapc IN $mapcs
OPTIONAL MATCH (ch:Chuong)-[:CO_DIEU]->(d)
OPTIONAL MATCH (dm:DeMuc)-[:CO_CHUONG]->(ch)
OPTIONAL MATCH (cd:ChuDe)-[:CO_DE_MUC]->(dm)
RETURN d.mapc AS mapc, d.ten AS ten, d.noidung AS noidung, d.vbqppl AS vbqppl,
       ch.ten AS tenchuong, dm.ten AS tendemuc, cd.ten AS tenchude
"""

_DIEU_SINGLE_CYPHER = """
MATCH (d:Dieu {mapc: $mapc})
OPTIONAL MATCH (ch:Chuong)-[:CO_DIEU]->(d)
OPTIONAL MATCH (dm:DeMuc)-[:CO_CHUONG]->(ch)
OPTIONAL MATCH (cd:ChuDe)-[:CO_DE_MUC]->(dm)
RETURN d.mapc AS mapc, d.ten AS ten, d.noidung AS noidung, d.vbqppl AS vbqppl,
       ch.ten AS tenchuong, dm.ten AS tendemuc, cd.ten AS tenchude
"""

# Tool definitions for OpenAI Chat Completions API
RAG_AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "vector_search",
            "description": (
                "Tìm điều luật trong Pháp Điển (hybrid: semantic Milvus + từ khoá BM25). "
                "Dùng khi cần tìm điều luật liên quan đến chủ đề; nên dùng thuật ngữ pháp lý cụ thể. "
                "Kết quả tự kèm các điều/khoản mà điều đứng đầu DẪN CHIẾU TỚI (trường `duoc_dan_chieu_tu`) và "
                "các khoản CẤM / NGOẠI LỆ liên quan (trường `ngoai_le_cua`)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Cụm từ tìm kiếm pháp lý"},
                    "top_k": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graph_traverse",
            "description": (
                "Mở rộng theo đồ thị từ các điều đã tìm được. Trường `quan_he` của mỗi kết quả: "
                "dan_chieu = điều gốc dẫn chiếu tới (căn cứ); duoc_dan_chieu = điều khác dẫn chiếu tới điều gốc "
                "(thường là văn bản hướng dẫn chi tiết); lien_quan = mục liên quan do Pháp Điển biên soạn. "
                "Dùng khi cần căn cứ dẫn chiếu, quy định hướng dẫn chi tiết hoặc điều liên quan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mapcs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Giá trị trường `mapc` (dãy số) của các điều đã tìm được; "
                                       "chấp nhận cả `ma_phap_dien` (vd. 20.2.LQ.35)",
                    },
                    "hop": {
                        "type": "integer",
                        "default": 1,
                        "description": "1 = quan hệ trực tiếp (khuyến nghị); 2 = thêm dẫn chiếu bắc cầu 2 bước",
                    },
                },
                "required": ["mapcs"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Tìm kiếm thông tin pháp luật mới nhất trên web. Dùng khi cần thông tin cập nhật hoặc văn bản chưa có trong Pháp Điển.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_full_article",
            "description": "Lấy toàn bộ nội dung một điều luật. Dùng khi cần đọc chi tiết hơn snippet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mapc": {"type": "string",
                             "description": "Giá trị trường `mapc` của điều luật; chấp nhận cả `ma_phap_dien`"},
                },
                "required": ["mapc"],
            },
        },
    },
]


# ── Tự động kiểm tra hiệu lực bằng web ─────────────────────────────────────────
# Pháp Điển trong DB có thể chưa cập nhật văn bản thay thế (vd. NĐ 100/2019 → NĐ 168/2024).
# Agent tự quyết gọi web_search thì hiếm khi gọi khi đã thấy một điều "khớp", nên trước khi
# trả lời, nếu câu hỏi thuộc loại dễ đổi theo văn bản mới hoặc kết quả Pháp Điển yếu → buộc
# gọi web_search đúng 1 lần.
AGENT_AUTO_WEB_VERIFY = os.getenv("AGENT_AUTO_WEB_VERIFY", "1").strip().lower() in ("1", "true", "yes")
AGENT_WEB_VERIFY_SCORE = float(os.getenv("AGENT_WEB_VERIFY_SCORE", "0.5"))
_TIME_SENSITIVE = re.compile(
    r"(phạt|xử phạt|bao nhiêu tiền|lệ phí|\bphí\b|\bthuế\b|mức lương|lương tối thiểu|mức đóng|"
    r"trợ cấp|mức hưởng|hiện nay|hiện hành|mới nhất|còn hiệu lực|năm 20\d\d)",
    re.IGNORECASE,
)
WEB_SEARCH_TOOL_CHOICE = {"type": "function", "function": {"name": "web_search"}}


def web_verify_reason(question: str, bucket: list[dict[str, Any]], used_tools: set[str]) -> str | None:
    """Lý do cần buộc kiểm tra web trước khi trả lời, hoặc None nếu không cần."""
    if not AGENT_AUTO_WEB_VERIFY or "web_search" in used_tools:
        return None
    phapdien = [h for h in bucket if h.get("source") == "phapdien"]
    if not phapdien:
        # Chỉ khi agent ĐÃ tra Pháp Điển mà không ra gì; câu chào / câu chỉ cần memory thì thôi
        searched = used_tools & {"vector_search", "graph_traverse", "get_full_article"}
        return "không tìm thấy điều luật phù hợp trong Pháp Điển" if searched else None
    if _TIME_SENSITIVE.search(question or ""):
        return ("câu hỏi về mức phạt / mức tiền / quy định hiện hành, có thể đã bị văn bản mới "
                "sửa đổi hoặc thay thế")
    top = max(float(h.get("score", 0.0)) for h in phapdien)
    if top < AGENT_WEB_VERIFY_SCORE:
        return f"độ liên quan của kết quả Pháp Điển thấp (cao nhất {top:.2f})"
    return None


def web_verify_instruction(reason: str) -> dict[str, str]:
    print(f"[Agent] Buộc web_search trước khi trả lời: {reason}", flush=True)
    return {
        "role": "system",
        "content": (
            f"Chưa trả lời. Lý do: {reason}. Gọi web_search để kiểm tra quy định hiện hành "
            "(văn bản đã bị sửa đổi/thay thế chưa, mức phạt/mức tiền hiện tại), sau đó mới viết "
            "câu trả lời cuối cùng theo đúng các nguyên tắc."
        ),
    }


def agent_chat_model() -> str:
    return (
        os.getenv("OPENAI_AGENT_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "gpt-4o-mini"
    )


def agent_system_prompt(*, situation_mode: bool) -> str:
    from datetime import datetime
    today = datetime.now().strftime("%d/%m/%Y")
    current_year = datetime.now().year
    base = f"""Bạn là trợ lý pháp luật Việt Nam thông minh với khả năng tìm kiếm đa chiều.

Hôm nay là {today} (năm {current_year}). Dùng thông tin này khi cần tính tuổi, thời hạn,
hoặc bất kỳ tính toán liên quan đến thời gian.

Quy trình làm việc:
1. Phân tích câu hỏi và lên kế hoạch tìm kiếm
2. Gọi vector_search để tìm điều luật liên quan
3. Nếu tìm thấy điều luật phù hợp, dùng graph_traverse để mở rộng context
4. Nếu cần thông tin cập nhật, gọi web_search
5. Nếu cần đọc chi tiết hơn, gọi get_full_article
6. Sau khi đủ thông tin (thường 2-4 tool calls), tổng hợp câu trả lời

Nguyên tắc:
- Luôn trích dẫn điều luật cụ thể khi có trong công cụ Pháp Điển.
- CÁCH TRÍCH DẪN: số điều và tên văn bản lấy NGUYÊN VĂN từ trường `vbqppl`
  (vd. "Điều 8 Nghị định số 100/2019/NĐ-CP" → ghi "Điều 8 Nghị định 100/2019/NĐ-CP"),
  kèm tên điều (trường `ten`). Trường `ma_phap_dien` (vd. "39.13.NĐ.75.8") chỉ là mã sắp xếp
  trong Bộ Pháp điển, KHÔNG phải số điều hay số hiệu văn bản: "NĐ.75" KHÔNG có nghĩa là
  "Nghị định 75", "39.13" KHÔNG phải "Điều 39.13". Thiếu `vbqppl` thì ghi "mã Pháp điển
  <ma_phap_dien>" + tên điều; TUYỆT ĐỐI không tự suy ra số điều, số hiệu hay năm văn bản.
- ĐỐI CHIẾU PHẠM VI ÁP DỤNG TRƯỚC KHI KẾT LUẬN: mỗi quy định chỉ áp dụng cho đúng
  chủ thể / phương tiện / hành vi / điều kiện nó nêu. So từng yếu tố với câu hỏi:
  · Khớp hoàn toàn → kết luận theo quy định đó.
  · Câu hỏi rộng hơn quy định (vd. hỏi "xe đạp", quy định chỉ nói "xe đạp máy, xe đạp điện")
    → TÁCH TRƯỜNG HỢP: trường hợp quy định có nói và trường hợp không nói. Không được
    gộp chúng, không suy rộng quy định sang đối tượng nó không nêu.
  · Không tìm thấy quy định nào cho trường hợp được hỏi → nói rõ "không có quy định
    xử phạt / không bắt buộc đối với trường hợp này trong các văn bản đã tra", không suy diễn.
- Chỉ đưa vào câu trả lời các khoản/điểm liên quan trực tiếp đến câu hỏi; không liệt kê
  các mức phạt cho hành vi khác.
- `diem_lien_quan` (0–1) của mỗi điều: dưới 0,5 là khớp yếu — đọc kỹ nội dung trước khi dùng.
- DẪN CHIẾU: kết quả có `duoc_dan_chieu_tu` là điều/khoản mà một điều khác dẫn chiếu tới (vd. "không
  thuộc trường hợp cấm kết hôn tại khoản 2 Điều 5"). Khi câu trả lời dựa vào điều có dẫn chiếu, NÊU RÕ
  nội dung được dẫn chiếu (liệt kê các trường hợp), đừng chỉ nhắc số điều/khoản.
- KIỂM TRA NGOẠI LỆ TRƯỚC KHI KẾT LUẬN "ĐƯỢC": kết quả có `ngoai_le_cua` là khoản CẤM / NGOẠI LỆ gắn với
  một điều khác (vd. điều kiện kết hôn ↔ cấm kết hôn giữa người có họ trong phạm vi ba đời; được sa thải ↔
  không được kỷ luật người lao động đang nghỉ ốm, mang thai). Đối chiếu tình huống của người dùng:
  · Rơi vào ngoại lệ → kết luận theo ngoại lệ (KHÔNG được / bị cấm), trích dẫn khoản đó.
  · Chưa rõ người dùng có thuộc ngoại lệ không → kết luận có điều kiện ("được, TRỪ KHI …") và nêu ngoại lệ.
  · Ngoại lệ không liên quan tới tình huống → bỏ qua, không cần nhắc.
- `noidung_truncated` = true: nội dung đã được rút gọn còn các khoản liên quan nhất; cần khoản bị lược
  thì gọi get_full_article.
- `van_ban_thay_the` (nếu có): văn bản chứa điều này đã bị thay thế/bãi bỏ — ưu tiên tìm quy định
  tương ứng trong văn bản mới (vector_search kèm số hiệu văn bản mới).
- PHÁP ĐIỂN VÀ WEB: Pháp Điển là nguồn chính về nội dung quy định nhưng có thể chưa cập nhật
  văn bản mới. Nếu web_search từ nguồn tin cậy (`do_tin_cay_nguon` = "high": vbpl.vn,
  chinhphu.vn, thuvienphapluat.vn…) cho thấy văn bản trong Pháp Điển đã bị sửa đổi/thay thế
  hoặc mức tiền đã đổi → nêu rõ quy định hiện hành theo văn bản mới (kèm nguồn), ghi chú quy
  định cũ theo Pháp Điển, và khuyên đối chiếu văn bản chính thức. Nguồn web độ tin cậy thấp
  hoặc không nói rõ văn bản → ưu tiên Pháp Điển.
- `tinh_trang_hieu_luc` (nếu có) là tình trạng văn bản theo sổ cái dữ liệu: văn bản ĐÃ HẾT
  HIỆU LỰC thì không dùng làm căn cứ chính — nói rõ đã hết hiệu lực và tìm văn bản thay thế
  được nêu; văn bản ĐÃ ĐƯỢC SỬA ĐỔI thì lưu ý người dùng nội dung có thể đã thay đổi.
- Nếu không đủ thông tin sau khi đã thử các công cụ phù hợp, thành thật nói không biết
- Không bịa thông tin pháp lý

CÁ NHÂN HOÁ DỰA TRÊN BỘ NHỚ NGƯỜI DÙNG (nếu có ở cuối prompt này):
- Bộ nhớ là nguồn sự thật về người dùng. KHÔNG hỏi lại thông tin đã có trong memory
  (năm sinh, tuổi, giới tính, trường học, nghề nghiệp…).
- Câu user có thể đã được viết lại thành cụm tra cứu pháp lý (mất "tôi/mình").
  Vẫn áp dụng memory nếu câu liên quan tuổi, điều kiện, quyền lợi, hoặc tình huống cá nhân.
- Nếu memory có năm sinh (kể cả câu viết chưa chuẩn, vd. chứa "sinh năm 2003") →
  TÍNH TUỔI = {current_year} − năm sinh, nêu rõ tuổi đã tính, rồi so sánh với điều luật.
- Nếu memory có nghề nghiệp/ngành/địa phương → liên hệ với quy định áp dụng cho nhóm đó.
- Kết luận RÕ RÀNG cho cá nhân: "Bạn ĐỦ tuổi", "Bạn CHƯA đủ tuổi", "Bạn THUỘC diện…",
  thay vì chỉ liệt kê quy định chung chung rồi hỏi lại tuổi — NHƯNG chỉ khi thông tin của
  người dùng khớp phạm vi áp dụng của quy định (xem ĐỐI CHIẾU PHẠM VI ÁP DỤNG). Còn thiếu
  một yếu tố quyết định (loại xe, loại hợp đồng…) → kết luận theo từng trường hợp.
- Vẫn trích dẫn điều luật làm căn cứ cho kết luận."""
    if situation_mode:
        base += """

Chế độ tình huống: người dùng mô tả một tình huống thực tế. Hãy:
- Chỉ ra các hành vi/căn cứ pháp lý có thể áp dụng
- Trích dẫn điều luật rõ ràng
- Tóm tắt rủi ro và hướng xử lý gợi ý (không thay thế tư vấn của luật sư)"""
    return base


def truncate_for_llm(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n… [đã rút gọn]"


def hit_dict_to_row(h: dict[str, Any]) -> dict[str, Any]:
    return {
        "mapc": h.get("mapc"),
        "ten": h.get("ten"),
        "noidung": h.get("noidung"),
        "tenchuong": h.get("tenchuong"),
        "tendemuc": h.get("tendemuc"),
        "tenchude": h.get("tenchude"),
        "vbqppl": h.get("vbqppl"),
    }


def _llm_score_passage(client: Any, query: str, passage: str, *, model: str | None = None) -> float:
    """Chấm liên quan query–passage (0–1), cùng logic TwoStageRetriever._score_pair."""
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    passage = (passage or "")[:8000]
    if not passage.strip():
        return 0.0
    system_prompt = (
        "Bạn là một hệ thống chấm điểm truy hồi văn bản phục vụ cho trả lời truy vấn pháp luật. "
        "Nhiệm vụ của bạn là đánh giá mức độ đoạn văn (passage) trả lời được câu hỏi (query). "
        "Đoạn văn nào trực tiếp cần dùng để trả lời câu hỏi thì điểm cao."
        "Hãy trả về MỘT số thực trong khoảng từ 0 đến 1:\n"
        "- 0: hoàn toàn không liên quan\n"
        "- 1: rất liên quan, trả lời trực tiếp câu hỏi\n"
        "Chỉ in ra đúng MỘT số, không giải thích gì thêm."
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Query:\n{query}\n\nPassage:\n{passage}"},
            ],
            temperature=0.0,
            max_tokens=8,
        )
        text = (resp.choices[0].message.content or "").strip()
        m = re.search(r"([01](?:\.\d+)?)", text)
        if not m:
            return 0.0
        return max(0.0, min(1.0, float(m.group(1))))
    except Exception:
        return 0.0


def _apply_llm_rerank_head(
    retriever: Any,
    query: str,
    ranked: list[dict[str, Any]],
    *,
    head_n: int = 12,
) -> list[dict[str, Any]]:
    """Chấm LLM lại phần đầu danh sách đã overlap-rerank, rồi gộp đuôi."""
    client = getattr(retriever, "client", None)
    if not client or not ranked:
        return ranked
    n = min(head_n, len(ranked))
    head = [dict(x) for x in ranked[:n]]
    tail = ranked[n:]
    for c in head:
        passage = (c.get("passage") or "").strip()
        if not passage:
            passage = "\n".join(
                filter(None, [c.get("ten"), c.get("noidung")])
            )
        c["score"] = _llm_score_passage(client, query, passage)
    head.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    return head + tail


def rerank_phapdien_hits(
    retriever: Any,
    rerank_query: str,
    full_hits: list[dict[str, Any]],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    """
    Sắp xếp lại passage Pháp Điển theo truy vấn người dùng:
    1) GraphRAGRetriever._rerank (overlap từ khóa + base_score)
    2) Tuỳ chọn: LLM chấm lại top-N (RAG_AGENT_LLM_RERANK=1)
    """
    q = (rerank_query or "").strip()
    if not full_hits or not q or not hasattr(retriever, "_rerank"):
        return full_hits

    candidates: list[dict[str, Any]] = []
    for i, h in enumerate(full_hits):
        base = float(h.get("score", max(0.35, 1.0 - i * 0.012)))
        candidates.append(
            {
                "mapc": h.get("mapc"),
                "ten": h.get("ten") or "",
                "noidung": (h.get("noidung") or "")[:12000],
                "passage": (h.get("passage") or "")[:12000],
                "tenchuong": h.get("tenchuong"),
                "tendemuc": h.get("tendemuc"),
                "tenchude": h.get("tenchude"),
                "vbqppl": h.get("vbqppl"),
                "base_score": base,
            }
        )

    pool = max(top_k, min(len(candidates), 40))
    ranked = retriever._rerank(candidates, q, top_k=pool)
    if GRAPH_BOOST_BETA > 0 and hasattr(retriever, "_graph_boost"):
        ranked = retriever._graph_boost(ranked, GRAPH_BOOST_BETA)
    if RAG_AGENT_LLM_RERANK and ranked:
        ranked = _apply_llm_rerank_head(retriever, q, ranked, head_n=12)

    out: list[dict[str, Any]] = []
    for c in ranked[:top_k]:
        row = {
            "mapc": c.get("mapc"),
            "ten": c.get("ten"),
            "noidung": c.get("noidung"),
            "tenchuong": c.get("tenchuong"),
            "tendemuc": c.get("tendemuc"),
            "tenchude": c.get("tenchude"),
            "vbqppl": c.get("vbqppl"),
        }
        out.append(row_to_full_hit(row, score=float(c.get("score", 0.5))))
    return out


def build_passage_text(row: dict[str, Any]) -> str:
    return "\n".join(
        filter(
            None,
            [
                row.get("tenchude"),
                row.get("tendemuc"),
                row.get("tenchuong"),
                row.get("ten"),
                row.get("noidung"),
            ],
        )
    )


def row_to_full_hit(row: dict[str, Any], *, score: float, source: str = "phapdien") -> dict[str, Any]:
    return {
        "mapc": row.get("mapc", ""),
        "ten": row.get("ten", ""),
        "tenchuong": row.get("tenchuong"),
        "tendemuc": row.get("tendemuc"),
        "tenchude": row.get("tenchude"),
        "vbqppl": row.get("vbqppl"),
        "noidung": row.get("noidung") or "",
        "passage": build_passage_text(row),
        "score": score,
        "source": source,
    }


_PHAPDIEN_CODE = re.compile(r"^\s*Điều\s+([\w.\-]+?)\.\s+(.*)$", re.UNICODE | re.DOTALL)


def split_phapdien_title(ten: str) -> tuple[str, str]:
    """'Điều 39.13.NĐ.75.8. Xử phạt…' → ('39.13.NĐ.75.8', 'Xử phạt…')."""
    m = _PHAPDIEN_CODE.match(ten or "")
    return (m.group(1), m.group(2).strip()) if m else ("", (ten or "").strip())


def row_to_llm_item(row: dict[str, Any], query: str = "") -> dict[str, Any]:
    """Bản rút gọn gửi LLM. Tách mã Pháp Điển khỏi tên điều để LLM không đọc nhầm mã
    (vd. 'NĐ.75') thành số hiệu văn bản; văn bản gốc nằm ở `vbqppl`. Không gửi kèm
    `passage` (trùng nội dung với `noidung`, tốn token gấp đôi).
    Điều dài: có `query` → giữ các khoản khớp truy vấn nhất (không cắt phần đầu)."""
    noidung = row.get("noidung") or ""
    if query:
        preview, truncated = select_relevant_text(noidung, query, LLM_MAX_NOIDUNG_CHARS)
    else:
        preview = truncate_for_llm(noidung, LLM_MAX_NOIDUNG_CHARS)
        truncated = len(noidung) > len(preview)
    ma, ten = split_phapdien_title(row.get("ten") or "")
    return {
        "mapc": row.get("mapc", ""),
        "ma_phap_dien": ma,
        "ten": ten,
        "vbqppl": row.get("vbqppl"),
        "tenchude": row.get("tenchude"),
        "tendemuc": row.get("tendemuc"),
        "tenchuong": row.get("tenchuong"),
        "noidung": preview,
        "noidung_truncated": truncated,
    }


_QUAN_HE_TEN = {
    "dan_chieu": "điều gốc dẫn chiếu tới điều này",
    "duoc_dan_chieu": "điều này dẫn chiếu tới điều gốc (thường là quy định hướng dẫn chi tiết)",
    "lien_quan": "mục liên quan do Pháp Điển biên soạn",
    "dan_chieu_2": "dẫn chiếu bắc cầu 2 bước",
}


def hit_to_llm_item(h: dict[str, Any], query: str = "") -> dict[str, Any]:
    """row_to_llm_item + điểm liên quan (0–1) để LLM biết kết quả nào yếu, kèm thông tin đồ thị
    (quan hệ với điều gốc, dẫn chiếu, văn bản thay thế) nếu có."""
    item = row_to_llm_item(hit_dict_to_row(h), query=query)
    item["diem_lien_quan"] = round(float(h.get("score", 0.0)), 2)
    if h.get("hieu_luc"):
        item["tinh_trang_hieu_luc"] = h["hieu_luc"]["ghi_chu"]
    if h.get("quan_he") in _QUAN_HE_TEN:
        item["quan_he"] = _QUAN_HE_TEN[h["quan_he"]]
    if h.get("dan_chieu_tu"):
        ma, _ = split_phapdien_title(h.get("ten_nguon") or "")
        where = " ".join(filter(None, [
            ("điểm " + ", ".join(h["diem"])) if h.get("diem") else "",
            ("khoản " + ", ".join(h["khoan"])) if h.get("khoan") else "",
        ]))
        item["duoc_dan_chieu_tu"] = (f"mã Pháp điển {ma}" if ma else h["dan_chieu_tu"]) + (
            f" (dẫn chiếu {where})" if where else "")
    if h.get("ngoai_le_cua"):
        ma, ten_nguon = split_phapdien_title(h.get("ten_nguon") or "")
        item["ngoai_le_cua"] = (f"mã Pháp điển {ma} ({ten_nguon})" if ma else h["ngoai_le_cua"]) + \
            " — khoản cấm/ngoại lệ, đối chiếu trước khi kết luận"
    if h.get("van_ban_thay_the"):
        v = h["van_ban_thay_the"]
        item["van_ban_thay_the"] = (f"{v['moi_nhat']} — có {v['so_dieu_trong_db']} điều trong CSDL"
                                    if v.get("so_dieu_trong_db") else f"{v['moi_nhat']} — chưa có trong CSDL")
    return item


_MA_PHAP_DIEN = re.compile(r"^\d+\.\d+\.[A-Za-zĐđ]+\.[\d.]+$")


def _lookup_mapc_by_code(codes: list[str]) -> dict[str, str]:
    """ma_phap_dien → mapc qua MySQL (tên điều bắt đầu bằng 'Điều <mã>. ')."""
    if not codes:
        return {}
    try:
        import pymysql
        conn = pymysql.connect(**_MYSQL_CFG)
        out: dict[str, str] = {}
        with conn.cursor() as cur:
            for code in codes:
                cur.execute("SELECT mapc FROM pddieu WHERE ten LIKE %s LIMIT 1", (f"Điều {code}. %",))
                row = cur.fetchone()
                if row:
                    out[code] = row[0]
        conn.close()
        return out
    except Exception as e:
        print(f"[Agent] Tra mapc theo mã Pháp Điển lỗi: {e}")
        return {}


def resolve_article_ids(ids: list[str]) -> list[str]:
    """LLM hay truyền `ma_phap_dien` (vd. '20.2.LQ.35') thay vì `mapc` → tra ra mapc tương ứng.
    id đã là mapc (dãy số) giữ nguyên; mã không tra được giữ nguyên để tool báo không tìm thấy."""
    ids = [i.strip().removeprefix("Điều ").rstrip(".") for i in ids if i and i.strip()]
    codes = [i for i in ids if _MA_PHAP_DIEN.match(i)]
    found = _lookup_mapc_by_code(codes)
    if codes:
        print(f"[Agent] Đổi mã Pháp Điển → mapc: {len(found)}/{len(codes)}", flush=True)
    return resolve_aliases([found.get(i, i) for i in ids])


def _fetch_dieu_mysql(mapcs: list[str]) -> list[dict[str, Any]]:
    if not mapcs:
        return []
    try:
        import pymysql
    except ImportError:
        return []
    placeholders = ",".join(["%s"] * len(mapcs))
    sql = f"""
        SELECT
            d.mapc AS mapc,
            cd.ten AS tenchude,
            dm.ten AS tendemuc,
            ch.ten AS tenchuong,
            d.ten AS ten,
            d.noidung AS noidung,
            d.vbqppl AS vbqppl
        FROM pddieu d
        LEFT JOIN pdchuong ch ON d.chuong_id = ch.mapc
        LEFT JOIN pddemuc dm ON d.demuc_id = dm.id
        LEFT JOIN pdchude cd ON d.chude_id = cd.id
        WHERE d.mapc IN ({placeholders})
    """
    try:
        conn = pymysql.connect(**_MYSQL_CFG)
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, mapcs)
            rows = list(cur.fetchall())
        conn.close()
        return rows
    except Exception:
        return []


def fetch_dieu_records(neo4j_driver: Any, mapcs: list[str]) -> list[dict[str, Any]]:
    """Lấy full điều luật + ngữ cảnh; giữ thứ tự mapcs từ Milvus."""
    ordered_ids = [str(m).strip() for m in mapcs if m]
    if not ordered_ids:
        return []

    by_mapc: dict[str, dict[str, Any]] = {}
    try:
        with neo4j_driver.session() as session:
            for r in session.run(_DIEU_CONTEXT_CYPHER, mapcs=ordered_ids):
                by_mapc[r["mapc"]] = dict(r)
    except Exception:
        pass

    missing = [m for m in ordered_ids if m not in by_mapc]
    for row in _fetch_dieu_mysql(missing):
        by_mapc[row["mapc"]] = row

    return [by_mapc[m] for m in ordered_ids if m in by_mapc]


def fetch_dieu_single(neo4j_driver: Any, mapc: str) -> dict[str, Any] | None:
    mapc = (mapc or "").strip()
    if not mapc:
        return None
    try:
        with neo4j_driver.session() as session:
            row = session.run(_DIEU_SINGLE_CYPHER, mapc=mapc).single()
        if row:
            return dict(row)
    except Exception:
        pass
    rows = _fetch_dieu_mysql([mapc])
    return rows[0] if rows else None


def merge_passage_hits(
    hits: list[dict[str, Any]],
    bucket: list[dict[str, Any]],
    *,
    max_extend: int = 12,
) -> None:
    seen: set[str] = set()
    for h in bucket:
        key = h.get("mapc") or h.get("url") or ""
        if key:
            seen.add(str(key))
    for h in hits[:max_extend]:
        key = h.get("mapc") or h.get("url") or (h.get("passage") or "")[:80]
        sk = str(key)
        if not sk or sk in seen:
            continue
        seen.add(sk)
        bucket.append(h)


def assistant_message_to_dict(msg: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"role": "assistant", "content": msg.content}
    tcalls = getattr(msg, "tool_calls", None)
    if tcalls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": getattr(tc, "type", None) or "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
            for tc in tcalls
        ]
    return out


def attach_newer_versions(retriever: Any, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gắn `van_ban_thay_the` cho điều thuộc văn bản đã bị thay thế/bãi bỏ (chuỗi THAY_THE trong đồ thị)."""
    if not hits or not hasattr(retriever, "_newest_versions"):
        return hits
    doc_of = {}
    for h in hits:
        p = parse_vbqppl(h.get("vbqppl"))
        if p:
            doc_of[id(h)] = p["so_hieu"]
    newer = retriever._newest_versions(list(doc_of.values())) if doc_of else {}
    for h in hits:
        v = newer.get(doc_of.get(id(h)))
        if v:
            h["van_ban_thay_the"] = v
    return hits


def reference_closure_hits(retriever: Any, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Điều/khoản mà top AGENT_REF_CLOSURE_SEEDS kết quả dẫn chiếu tới (đồ thị THAM_CHIEU), chưa có trong hits."""
    if not AGENT_REF_CLOSURE or not hits or not hasattr(retriever, "_reference_closure"):
        return []
    seeds = [h["mapc"] for h in hits[:AGENT_REF_CLOSURE_SEEDS] if h.get("mapc")]
    score_of = {h.get("mapc"): float(h.get("score", 0.0)) for h in hits}
    rows = retriever._reference_closure(seeds, exclude={h.get("mapc") for h in hits},
                                        limit=AGENT_REF_CLOSURE_MAX, max_chars=AGENT_REF_CLOSURE_CHARS)
    out = []
    for r in rows:
        h = row_to_full_hit(r, score=round(0.8 * score_of.get(r.get("dan_chieu_tu"), 0.5), 4))
        h.update({k: r.get(k) for k in ("dan_chieu_tu", "ten_nguon", "khoan", "diem")})
        h["quan_he"] = "dan_chieu"
        out.append(h)
    return annotate_hits(out) if out else out


def exception_hits(retriever: Any, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Khoản CẤM / NGOẠI LỆ nối (đồ thị) với top AGENT_EXCEPTION_SEEDS kết quả, chưa có trong hits."""
    if not AGENT_EXCEPTION_CHECK or not hits or not hasattr(retriever, "_exception_check"):
        return []
    seeds = [h["mapc"] for h in hits[:AGENT_EXCEPTION_SEEDS] if h.get("mapc") and h.get("source", "phapdien") != "web"]
    score_of = {h.get("mapc"): float(h.get("score", 0.0)) for h in hits}
    rows = retriever._exception_check(seeds, exclude={h.get("mapc") for h in hits},
                                      limit=AGENT_EXCEPTION_MAX, max_chars=AGENT_REF_CLOSURE_CHARS)
    out = []
    for r in rows:
        h = row_to_full_hit(r, score=round(0.8 * score_of.get(r.get("ngoai_le_cua"), 0.5), 4))
        h.update({k: r.get(k) for k in ("ngoai_le_cua", "ten_nguon", "khoan")})
        h["quan_he"] = "ngoai_le"
        out.append(h)
    return annotate_hits(out) if out else out


def execute_agent_tool(
    name: str,
    args: dict[str, Any],
    *,
    retriever: Any,
    neo4j_driver: Any,
    rerank_query: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    """
  Thực thi tool.
  Returns:
    - JSON rút gọn cho LLM (tool message)
    - full hits cho bucket UI
    """
    args = args or {}
    empty: list[dict[str, Any]] = []
    try:
        if name == "vector_search":
            q = str(args.get("query", "")).strip()
            top_k = int(args.get("top_k", 10))
            if not q:
                return json.dumps([], ensure_ascii=False), empty

            pool_k = min(max(top_k * AGENT_VECTOR_POOL_MULT, top_k), 40)
            # Hybrid (Milvus + BM25, RRF) — giữ điểm retrieval thật làm base_score cho rerank
            if hasattr(retriever, "_hybrid_search"):
                fused = retriever._hybrid_search(q, top_k=pool_k)
                score_by_mapc = {d["mapc"]: float(d["score"]) for d in fused}
                mapcs = [d["mapc"] for d in fused]
            else:
                mapcs = retriever._vector_search(q, top_k=pool_k)
                score_by_mapc = {m: max(0.35, 1.0 - i * 0.012) for i, m in enumerate(mapcs)}
            if not mapcs:
                return json.dumps([], ensure_ascii=False), empty

            rows = fetch_dieu_records(neo4j_driver, mapcs)
            full_hits = [row_to_full_hit(r, score=score_by_mapc.get(r.get("mapc"), 0.3)) for r in rows]
            # Rerank theo truy vấn agent tự đặt (q) + truy vấn gốc đã chuẩn hoá: agent hay
            # tách câu hỏi thành nhiều lượt search hẹp, chỉ dùng rerank_query sẽ kéo mọi
            # lượt về cùng một thứ tự.
            rq = " ".join(dict.fromkeys(filter(None, [q, (rerank_query or "").strip()])))
            full_hits = annotate_hits(rerank_phapdien_hits(retriever, rq, full_hits, top_k=top_k))
            # Ngoại lệ trước: điều vừa được dẫn chiếu vừa là khoản cấm thì gắn nhãn ngoại lệ (quan trọng hơn)
            full_hits = full_hits + exception_hits(retriever, full_hits)
            full_hits = full_hits + reference_closure_hits(retriever, full_hits)
            attach_newer_versions(retriever, full_hits)
            llm_items = [hit_to_llm_item(h, query=rq) for h in full_hits]
            return json.dumps(llm_items, ensure_ascii=False), full_hits

        if name == "graph_traverse":
            mapcs = args.get("mapcs") or []
            if not isinstance(mapcs, list):
                mapcs = []
            mapcs = resolve_article_ids([str(x) for x in mapcs if x])
            hop = min(2, max(1, int(args.get("hop", 1))))
            # Bỏ chính các điều gốc: agent đã có nội dung của chúng, gửi lại chỉ tốn token
            seeds = set(mapcs)
            results = [r for r in retriever._graph_expand(mapcs, hop=hop)[:40] if r.get("mapc") not in seeds]
            quan_he = {r.get("mapc"): r.get("quan_he") for r in results}

            full_hits = []
            for r in results:
                row = {
                    "mapc": r.get("mapc"),
                    "ten": r.get("ten"),
                    "noidung": r.get("noidung"),
                    "tenchuong": r.get("tenchuong"),
                    "tendemuc": r.get("tendemuc"),
                    "tenchude": r.get("tenchude"),
                    "vbqppl": r.get("vbqppl"),
                }
                score = float(r.get("base_score", 0.85))
                full_hits.append(row_to_full_hit(row, score=score))

            graph_top = min(len(full_hits), 20) if full_hits else 0
            rq = (rerank_query or "").strip()
            if graph_top:
                full_hits = annotate_hits(rerank_phapdien_hits(retriever, rq or " ".join(mapcs[:5]),
                                                               full_hits, top_k=graph_top))
            for h in full_hits:
                if quan_he.get(h.get("mapc")):
                    h["quan_he"] = quan_he[h["mapc"]]
            attach_newer_versions(retriever, full_hits)
            llm_items = [hit_to_llm_item(h, query=rq) for h in full_hits]

            return json.dumps(llm_items, ensure_ascii=False), full_hits

        if name == "web_search":
            from retrieve.tavily_fallback import search_tavily

            q = str(args.get("query", "")).strip()
            if not q:
                return json.dumps([], ensure_ascii=False), empty

            res = search_tavily(q, top_k=5)
            print(f"[Agent] web_search {q!r} → {len(res)} kết quả sau lọc tin cậy", flush=True)
            full_hits = []
            llm_items = []
            for r in res:
                content = r.get("noidung") or ""
                full_hits.append({
                    "mapc": r.get("mapc") or "",
                    "ten": r.get("ten") or "",
                    "noidung": content,
                    "passage": content,
                    "score": 0.7,
                    "source": "web",
                    "url": r.get("url", ""),
                    "trust_level": r.get("trust_level", "medium"),
                    "source_label": r.get("source_label", "[Web]"),
                })
                llm_items.append({
                    "ten": r.get("ten"),
                    "noidung": truncate_for_llm(content, LLM_MAX_WEB_CHARS),
                    "url": r.get("url", ""),
                    "do_tin_cay_nguon": r.get("trust_level", "medium"),
                })
            return json.dumps(llm_items, ensure_ascii=False), full_hits

        if name == "get_full_article":
            mapc = str(args.get("mapc", "")).strip()
            if not mapc:
                return json.dumps({"error": "Thiếu mapc"}, ensure_ascii=False), empty
            mapc = (resolve_article_ids([mapc]) or [mapc])[0]

            row = fetch_dieu_single(neo4j_driver, mapc)
            if not row:
                return json.dumps({"error": "Không tìm thấy"}, ensure_ascii=False), empty

            full_hit = attach_newer_versions(retriever, annotate_hits([row_to_full_hit(row, score=1.0)]))[0]
            ma, ten_dieu = split_phapdien_title(row.get("ten") or "")
            llm_payload = {
                "mapc": mapc,
                "ma_phap_dien": ma,
                "ten": ten_dieu,
                "tenchuong": row.get("tenchuong"),
                "tendemuc": row.get("tendemuc"),
                "tenchude": row.get("tenchude"),
                "vbqppl": row.get("vbqppl"),
                "noidung": row.get("noidung") or "",
            }
            if full_hit.get("hieu_luc"):
                llm_payload["tinh_trang_hieu_luc"] = full_hit["hieu_luc"]["ghi_chu"]
            if full_hit.get("van_ban_thay_the"):
                llm_payload["van_ban_thay_the"] = hit_to_llm_item(full_hit)["van_ban_thay_the"]
            return json.dumps(llm_payload, ensure_ascii=False), [full_hit]

    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False), empty

    return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False), empty


def apply_agent_tool_result(
    tool_name: str,
    tool_content: str,
    full_hits: list[dict[str, Any]],
    bucket: list[dict[str, Any]],
    *,
    max_extend: int = 12,
) -> None:
    """Gộp full hits vào bucket; fallback parse JSON nếu không có hits."""
    if full_hits:
        merge_passage_hits(full_hits, bucket, max_extend=max_extend)
    else:
        merge_tool_result_into_passages(
            tool_name, tool_content, bucket, max_extend=max_extend
        )


def hits_for_passages(tool_name: str, result_json: str) -> list[dict[str, Any]]:
    """Chuẩn hoá kết quả tool thành dict dùng được với _build_passages."""
    try:
        data = json.loads(result_json)
    except Exception:
        return []

    out: list[dict[str, Any]] = []

    if tool_name == "get_full_article":
        if not isinstance(data, dict) or data.get("error"):
            return []
        out.append({
            "mapc": data.get("mapc", ""),
            "ten": data.get("ten", ""),
            "noidung": data.get("noidung", ""),
            "passage": "\n".join(filter(None, [data.get("ten"), data.get("noidung")])),
            "score": 1.0,
            "source": "phapdien",
        })
        return out

    if not isinstance(data, list):
        return []

    for r in data:
        if not isinstance(r, dict):
            continue
        url = r.get("url", "")
        if url or tool_name == "web_search":
            out.append({
                "mapc": r.get("mapc") or "",
                "ten": r.get("ten") or r.get("title") or "",
                "noidung": r.get("noidung") or r.get("content") or "",
                "passage": r.get("passage")
                or "\n".join(filter(None, [r.get("title"), r.get("content"), r.get("noidung")])),
                "score": float(r.get("score", 0.7)),
                "source": "web",
                "url": url,
                "trust_level": r.get("trust_level", "medium"),
                "source_label": r.get("source_label", "[Web]"),
            })
        elif tool_name == "graph_traverse":
            out.append({
                "mapc": r.get("mapc", ""),
                "ten": r.get("ten", ""),
                "tenchuong": r.get("tenchuong"),
                "tendemuc": r.get("tendemuc"),
                "tenchude": r.get("tenchude"),
                "vbqppl": r.get("vbqppl"),
                "noidung": r.get("noidung") or r.get("passage", ""),
                "passage": r.get("passage") or r.get("noidung", ""),
                "score": 0.85,
                "source": "phapdien",
            })
        else:
            # vector_search
            out.append({
                "mapc": r.get("mapc", ""),
                "ten": r.get("ten", ""),
                "noidung": r.get("noidung", ""),
                "passage": "\n".join(filter(None, [r.get("ten"), r.get("noidung")])),
                "score": 0.8,
                "source": "phapdien",
            })
    return out


def merge_tool_result_into_passages(
    tool_name: str,
    result_json: str,
    bucket: list[dict[str, Any]],
    *,
    max_extend: int = 8,
) -> None:
    """Fallback: parse JSON tool (thường bản rút gọn) vào bucket."""
    hits = hits_for_passages(tool_name, result_json)
    merge_passage_hits(hits, bucket, max_extend=max_extend)


SYNTHESIS_PROMPT = "Hãy tổng hợp câu trả lời dựa trên thông tin đã thu thập (tiếng Việt)."
# Stream: giữ lại phần chữ đầu tiên tới khi chắc lượt này là câu trả lời (không kèm tool call) rồi mới đẩy ra —
# model đôi khi viết một câu dạo đầu rồi mới gọi tool, câu đó không được hiện như câu trả lời.
STREAM_HOLD_CHARS = int(os.getenv("AGENT_STREAM_HOLD_CHARS", "80"))


def _tool_call_ns(tid: str, name: str, arguments: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(id=tid, type="function", function=SimpleNamespace(name=name, arguments=arguments or "{}"))


def _stream_turn(openai_client: Any, kw: dict[str, Any]):
    """
    Một lượt gọi model ở chế độ stream. Yield:
      ("token", text)            — chỉ khi chắc chắn lượt này là câu trả lời
      ("end", tool_calls, text)  — cuối lượt: tool_calls (list|None), toàn bộ text
    """
    acc: dict[int, dict[str, str]] = {}
    parts: list[str] = []
    held: list[str] = []
    flushed = False
    for chunk in openai_client.chat.completions.create(stream=True, **kw):
        if not getattr(chunk, "choices", None):
            continue
        delta = chunk.choices[0].delta
        for tc in getattr(delta, "tool_calls", None) or []:
            a = acc.setdefault(tc.index, {"id": "", "name": "", "args": ""})
            a["id"] = tc.id or a["id"]
            fn = getattr(tc, "function", None)
            if fn is not None:
                a["name"] += fn.name or ""
                a["args"] += fn.arguments or ""
        text = getattr(delta, "content", None)
        if not text:
            continue
        parts.append(text)
        if acc:
            continue
        if flushed:
            yield ("token", text)
        else:
            held.append(text)
            if sum(map(len, held)) >= STREAM_HOLD_CHARS:
                flushed = True
                yield ("token", "".join(held))
    if flushed and acc:
        print("[Agent] Model viết chữ rồi mới gọi tool — phần chữ đã lỡ hiển thị", flush=True)
    if not acc and not flushed and held:
        yield ("token", "".join(held))
    calls = [_tool_call_ns(a["id"], a["name"], a["args"]) for _, a in sorted(acc.items())] or None
    yield ("end", calls, "".join(parts))


def _answer_turn(openai_client: Any, kw: dict[str, Any], stream: bool):
    """Gọi model một lượt; yield ("token", …) rồi ("end", tool_calls, text). Không stream → một token duy nhất."""
    if stream:
        yield from _stream_turn(openai_client, kw)
        return
    msg = openai_client.chat.completions.create(**kw).choices[0].message
    text = msg.content or ""
    if not msg.tool_calls and text.strip():
        yield ("token", text.strip())
    yield ("end", msg.tool_calls or None, text)


def agent_events(
    *,
    openai_client: Any,
    retriever: Any,
    neo4j_driver: Any,
    user_prompt: str,
    history: list[dict[str, str]],
    situation_mode: bool = False,
    max_iterations: int = 6,
    rerank_query: str | None = None,
    system_prompt_extra: str = "",
    stream: bool = False,
):
    """
    Vòng lặp tool-calling DUY NHẤT của agent (dùng cho cả /rag/chat và /rag/stream). Yield các sự kiện:
      {"type": "tool", "tool", "call_id", "args", "iteration", "max"}
      {"type": "tool_done", "tool", "call_id", "count", "titles", "iteration", "max"}
      {"type": "verify", "reason", "iteration", "max"}              buộc kiểm tra web ở lượt này
      {"type": "compose", "iteration", "max", "passage_hits"}       ngay trước token đầu tiên của câu trả lời
      {"type": "token", "text"}
      {"type": "final", "answer", "passage_hits", "iterations"}

    Kiểm tra web (web_verify_reason) chạy TRƯỚC khi model viết câu trả lời: sau mỗi vòng tool, nếu câu hỏi dễ đổi
    theo văn bản mới hoặc Pháp Điển yếu thì lượt kế tiếp buộc gọi web_search — không sinh rồi bỏ bản nháp.
    stream=True → token đẩy ra ngay khi model sinh (stream thật).
    system_prompt_extra: đoạn bổ sung cuối system prompt (long-term memory của người dùng).
    """
    rerank_ctx = (rerank_query if rerank_query is not None else user_prompt) or ""
    model = agent_chat_model()
    system_prompt = agent_system_prompt(situation_mode=situation_mode)
    if system_prompt_extra:
        system_prompt = system_prompt + "\n\n" + system_prompt_extra
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *[{"role": m["role"], "content": m["content"]} for m in history[-8:]],
        {"role": "user", "content": user_prompt},
    ]
    bucket: list[dict[str, Any]] = []
    used_tools: set[str] = set()
    tool_rounds = 0

    def answer(turn, iteration: int):
        """Đẩy compose + token; trả text cuối (hoặc None nếu lượt này là tool call)."""
        started = False
        for ev in turn:
            if ev[0] == "token":
                if not started:
                    started = True
                    yield {"type": "compose", "iteration": iteration, "max": max_iterations, "passage_hits": bucket}
                yield {"type": "token", "text": ev[1]}
            else:
                return ev[1], ev[2]
        return None, ""

    for iteration in range(max_iterations):
        tool_choice: Any = "auto"
        reason = web_verify_reason(user_prompt, bucket, used_tools) if tool_rounds else None
        if reason and iteration < max_iterations - 1:
            messages.append(web_verify_instruction(reason))
            tool_choice = WEB_SEARCH_TOOL_CHOICE
            used_tools.add("web_search")  # chỉ buộc 1 lần
            yield {"type": "verify", "reason": reason, "iteration": iteration + 1, "max": max_iterations}

        kw = {"model": model, "messages": messages, "tools": RAG_AGENT_TOOLS,
              "tool_choice": tool_choice, "temperature": 0.1}
        tool_calls, text = yield from answer(_answer_turn(openai_client, kw, stream), iteration + 1)

        if not tool_calls:
            messages.append({"role": "assistant", "content": text})
            if text.strip():
                yield {"type": "final", "answer": text.strip(), "passage_hits": bucket, "iterations": iteration + 1}
                return
            break  # model trả rỗng → lượt tổng hợp

        messages.append(assistant_message_to_dict(_tool_call_msg(text, tool_calls)))
        tool_rounds += 1
        for tc in tool_calls:
            name = tc.function.name
            used_tools.add(name)
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            yield {"type": "tool", "tool": name, "call_id": tc.id, "args": args,
                   "iteration": iteration + 1, "max": max_iterations}
            tool_content, full_hits = execute_agent_tool(
                name, args, retriever=retriever, neo4j_driver=neo4j_driver, rerank_query=rerank_ctx,
            )
            apply_agent_tool_result(name, tool_content, full_hits, bucket)
            yield {"type": "tool_done", "tool": name, "call_id": tc.id, "count": len(full_hits or []),
                   "titles": [str(h.get("ten") or h.get("title") or "")[:80] for h in (full_hits or [])[:5]],
                   "iteration": iteration + 1, "max": max_iterations}
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_content})

    kw = {"model": model, "temperature": 0.2, "max_tokens": 1200,
          "messages": messages + [{"role": "user", "content": SYNTHESIS_PROMPT}]}
    _, text = yield from answer(_answer_turn(openai_client, kw, stream), max_iterations)
    yield {"type": "final", "answer": (text or "").strip(), "passage_hits": bucket, "iterations": max_iterations}


def _tool_call_msg(content: str, tool_calls: list) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(content=content or None, tool_calls=tool_calls)


def run_agentic_rag_sync(
    *,
    openai_client: Any,
    retriever: Any,
    neo4j_driver: Any,
    user_prompt: str,
    history: list[dict[str, str]],
    situation_mode: bool = False,
    max_iterations: int = 6,
    rerank_query: str | None = None,
    system_prompt_extra: str = "",
) -> dict[str, Any]:
    """Bản không stream của agent_events: trả {answer, passage_hits, iterations}."""
    final: dict[str, Any] = {"answer": "", "passage_hits": [], "iterations": max_iterations}
    for ev in agent_events(openai_client=openai_client, retriever=retriever, neo4j_driver=neo4j_driver,
                           user_prompt=user_prompt, history=history, situation_mode=situation_mode,
                           max_iterations=max_iterations, rerank_query=rerank_query,
                           system_prompt_extra=system_prompt_extra, stream=False):
        if ev["type"] == "final":
            final = {k: ev[k] for k in ("answer", "passage_hits", "iterations")}
    return final
