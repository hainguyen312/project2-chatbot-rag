from typing import Any, Dict, List, Optional
import os
import re
from .base_agent import BaseAgent
from transformers import AutoModelForSequenceClassification, AutoTokenizer
import torch
import torch.nn.functional as F


def select_device(preference: Optional[str] = None) -> str:
    """Chọn device cho model sentiment: "cuda" nếu có GPU, ngược lại "cpu".

    preference (mặc định lấy env SENTIMENT_DEVICE): "auto" | "cuda" | "cpu".
    "cuda" khi không có GPU → fallback "cpu" kèm cảnh báo; giá trị lạ → coi như "auto".
    """
    pref = (preference if preference is not None else os.getenv("SENTIMENT_DEVICE", "auto"))
    pref = (pref or "auto").strip().lower()
    if pref not in ("auto", "cuda", "cpu"):
        print(f"[Sentiment] SENTIMENT_DEVICE={pref!r} không hợp lệ, dùng auto", flush=True)
        pref = "auto"
    if pref == "cpu":
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if pref == "cuda":
        print("[Sentiment] Yêu cầu cuda nhưng không có GPU, fallback cpu", flush=True)
    return "cpu"


class SentimentAgent(BaseAgent):
    """Phát hiện cảm xúc tiêu cực bằng rule heuristic."""

    def __init__(self):
        super().__init__("sentiment")
        model_name = "joeddav/xlm-roberta-large-xnli"
        self.labels = {
            "neutral_info": "Người dùng đang có yêu cầu hỏi thông tin pháp luật.",
            "satisfied": "Người dùng đang cảm thấy hài lòng với hệ thống.",
            #   "confused": "Người dùng đang bối rối.",
            #   "anxious": "Người dùng đang lo lắng.",
            "urgent": "Người dùng đang khẩn cấp.",
            "sad": "Người dùng đang cảm thấy buồn.",
            "dissatisfied": "Người dùng đang không hài lòng với hệ thống.",
            "angry": "Người dùng đang cảm thấy tức giận hay bực bội cá nhân.",
            "grateful_polite": "Người dùng đang cảm ơn hoặc lịch sự.",
            "hostile_abusive": "Người dùng đang thù hằn hoặc thô tục."
            }
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.device = select_device()
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name).to(self.device)
        self.model.eval()
        gpu = f" ({torch.cuda.get_device_name(0)})" if self.device == "cuda" else ""
        print(f"[Sentiment] device: {self.device}{gpu}", flush=True)
        
        # self.negative_keywords = [
        #     "thật tệ", "không hài lòng", "bực mình", "vô nghĩa",
        #     "vô ích", "quá kém", "tức giận", "không tin"
        # ]
        # self.threshold = 0.5

    def _normalize(self, text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"\s+", " ", text)
        return text
    
    def _zero_shot_sentiment_vi(self, text: str, tau=0.2, k=2) -> Dict[str, Any]:
        hyp = list(self.labels.values())
        premise = [text] * len(hyp)
        enc = self.tokenizer(premise, hyp, padding=True, truncation=True, return_tensors="pt").to(self.device)
        with torch.no_grad():
            logits = self.model(**enc).logits  # (n,3): [contradiction, neutral, entailment]
        probs = F.softmax(logits, dim=-1)[:, 2]  # entailment, shape: (n,)

        # top-k theo entailment
        k = min(k, probs.shape[0])
        vals, idx = torch.topk(probs, k=k)  # vals: (k,), idx: (k,)
        keys = list(self.labels.keys())

        top_k = [
            {"label": keys[int(i)], "confidence": round(float(v), 4)}
            for v, i in zip(vals.tolist(), idx.tolist())
            if float(v) >= tau
        ]

        if top_k:
            return {"top_k": top_k}

        # nếu tất cả dưới ngưỡng, trả ứng viên tốt nhất vào undetermined
        best_i = int(torch.argmax(probs))
        best_score = float(probs[best_i])
        return {
            "top_k": [],
            "undetermined": {"label": keys[best_i], "confidence": round(best_score, 4)}
        }

    def run(self, prompt: str, history: Optional[List[Any]] = None) -> Dict[str, Any]:
        try:
            # p = self._normalize(prompt)
            # score = sum(k in p for k in self.negative_keywords) / len(self.negative_keywords)
            # score = min(1.0, float(score))
            # return {
            #     "type": "sentiment",
            #     "score": score,
            #     "is_negative": score >= self.threshold,
            # }

            input_text = f"Người dùng gửi tin nhắn cho hệ thống là: {prompt}"
            output = self._zero_shot_sentiment_vi(input_text)

            NEGATIVE_LABELS = {"sad", "dissatisfied", "angry", "hostile_abusive"}

            is_negative = False
            is_find_info = False 
            sentiment_type: List[str] = []

            for u in output.get("top_k", []):
                label = u.get("label")
                if not label:
                    continue
                sentiment_type.append(label)
                if label == "neutral_info":
                    is_find_info = True
                if label in NEGATIVE_LABELS:
                    is_negative = True
            return {
                "type": "sentiment",
                "sentiment_type": sentiment_type,
                "is_negative": is_negative,
                "is_findinfo": is_find_info
            }
        except Exception:
            return {
                "type": "sentiment",
                "sentiment_type": "neutral_info",
                "is_negative": False,
                "is_findinfo": True
            }



