import re
from typing import Any, Dict, List, Optional
from .agents_manager import AgentsManager
from .types import Decision, Action
from .quick_agent import meta_conversation_fallback, try_quick_answer

_manager = AgentsManager()

def normalize(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t

def run_pre_retrieve(prompt: str, history: Optional[List[Any]] = None) -> Decision:
    """
    Chạy tầng pre-retrieve để xác định hướng xử lý trước khi gửi vào RAG.
    Trả về Decision với cùng cấu trúc như pipeline.py gốc.
    """
    # Normalize prompt
    p_norm = prompt.strip().lower()

    # Chào hỏi / meta — ưu tiên trước cache & RAG
    quick_ans = try_quick_answer(prompt)
    if quick_ans:
        return Decision(
            action=Action.QUICK_ANSWER,
            reason="quick_question_match",
            answer_text=quick_ans,
            normalized_prompt=p_norm,
        )
    meta_ans = meta_conversation_fallback(prompt)
    if meta_ans:
        return Decision(
            action=Action.QUICK_ANSWER,
            reason="meta_conversation",
            answer_text=meta_ans,
            normalized_prompt=p_norm,
        )

    results: Dict[str, Any] = _manager.analyze(prompt, history or [])
    action = results["action"]

    # Quyết định action theo cấu trúc gốc
    if action == "quick":
        return Decision(
            action=Action.QUICK_ANSWER,
            reason="quick_question_match",
            answer_text=results.get("answer_text", ""),
            normalized_prompt=p_norm,
        )
    
    if action == "spam":
        return Decision(
            action=Action.SPAM,
            reason=results.get("reason", ""),
            normalized_prompt=p_norm,
        )

    if action == "escalate":
        return Decision(
            action=Action.ESCALATE,
            reason=results.get("reason", ""),
            normalized_prompt=p_norm,
        )

    # B3: Mặc định — tiếp tục pipeline RAG
    return Decision(
        action=Action.PROCEED,
        sentiment=results.get("sentiment", ""),
        normalized_prompt=p_norm,
    )
