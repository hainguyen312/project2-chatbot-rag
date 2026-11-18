"""
Bảng giá model ($ / 1M token) + tính chi phí và ước tính "nếu đổi model" — không gọi API.

Giá mặc định ở DEFAULT_PRICES; admin sửa trên UI → lưu đè vào evaluation/pricing.json
(file này thắng mặc định). Khi giá nhà cung cấp đổi, chỉ cần sửa trên trang admin.

Đổi model cho một lần chạy cũ:
  - có số token (lần chạy mới, script đã ghi usage) → tính lại chính xác bằng giá model mới;
  - chỉ có số tiền → quy đổi theo tỉ lệ giá input; CHÍNH XÁC khi hai model có cùng tỉ lệ
    giá output/input (đúng với mọi model trong bảng mặc định), ngược lại đánh dấu "xấp xỉ".
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Optional

from .suites import EVAL_DIR

PRICING_PATH = os.path.join(EVAL_DIR, "pricing.json")
BATCH_DISCOUNT = 0.5  # Anthropic Message Batches API: -50%

# provider, input $/1M, output $/1M, nguồn
DEFAULT_PRICES: dict[str, dict[str, Any]] = {
    # Claude — đối chiếu tài liệu Anthropic (bảng model, cache 2026-11-03). Haiku 5.5: prompt ≤ 100K token.
    "claude-opus-5-5": {"provider": "anthropic", "in": 4.0, "out": 20.0, "source": "Anthropic docs 2026-11"},
    "claude-sonnet-5-5": {"provider": "anthropic", "in": 2.0, "out": 10.0, "source": "Anthropic docs 2026-11"},
    "claude-haiku-5-5": {"provider": "anthropic", "in": 0.10, "out": 0.50, "source": "Anthropic docs 2026-11"},
    # OpenAI — lấy từ evaluation/eval_tool_calls.py (chưa đối chiếu lại; sửa trên trang admin nếu đổi)
    "gpt-4.1": {"provider": "openai", "in": 2.0, "out": 8.0, "source": "repo"},
    "gpt-4.1-mini": {"provider": "openai", "in": 0.40, "out": 1.60, "source": "repo"},
    "gpt-4o": {"provider": "openai", "in": 2.5, "out": 10.0, "source": "repo"},
    "gpt-4o-mini": {"provider": "openai", "in": 0.15, "out": 0.60, "source": "repo"},
    # Embedding (bộ nhớ, truy vấn vector) — chưa đối chiếu; provider riêng để không lẫn với model chat khi "đổi model".
    "text-embedding-3-small": {"provider": "openai-embedding", "in": 0.02, "out": 0.0, "source": "chưa đối chiếu"},
}

_lock = threading.Lock()


def load_prices() -> dict[str, dict[str, Any]]:
    prices = {k: dict(v) for k, v in DEFAULT_PRICES.items()}
    try:
        with open(PRICING_PATH, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    for model, p in (saved.get("models") or {}).items():
        if p is None:
            prices.pop(model, None)
        else:
            prices[model] = {**prices.get(model, {}), **p}
    return prices


def save_prices(models: dict[str, Optional[dict]]) -> dict[str, dict[str, Any]]:
    clean: dict[str, Optional[dict]] = {}
    for model, p in (models or {}).items():
        model = str(model).strip()
        if not model or len(model) > 80:
            raise ValueError("Tên model không hợp lệ")
        if p is None:
            clean[model] = None
            continue
        try:
            pin, pout = float(p["in"]), float(p["out"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"{model}: giá input/output phải là số")
        if pin < 0 or pout < 0 or pin > 1000 or pout > 1000:
            raise ValueError(f"{model}: giá ngoài khoảng hợp lệ")
        provider = str(p.get("provider") or "").strip()[:30] or "other"
        clean[model] = {"provider": provider, "in": pin, "out": pout, "source": str(p.get("source") or "admin")[:60]}
    with _lock:
        tmp = PRICING_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"models": clean}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, PRICING_PATH)
    return load_prices()


def cost(model: str, tokens_in: float, tokens_out: float, batch: bool = False,
         prices: Optional[dict] = None) -> Optional[float]:
    p = (prices or load_prices()).get(model)
    if not p:
        return None
    return (tokens_in * p["in"] + tokens_out * p["out"]) / 1e6 * (BATCH_DISCOUNT if batch else 1.0)


def reprice(item: dict, target: str, prices: dict) -> tuple[Optional[float], bool]:
    """Chi phí của một khoản (item) nếu dùng model `target`. Trả (usd, chính_xác)."""
    if item.get("tokens_in") is not None and item.get("tokens_out") is not None:
        return cost(target, item["tokens_in"], item["tokens_out"], item.get("batch", False), prices), True
    src, dst = prices.get(item.get("model") or ""), prices.get(target)
    if not src or not dst or item.get("usd") is None or not src["in"]:
        return None, False
    exact = abs(src["out"] * dst["in"] - dst["out"] * src["in"]) < 1e-9
    return item["usd"] * dst["in"] / src["in"], exact


def what_if(items: list[dict], prices: Optional[dict] = None) -> list[dict]:
    """Mỗi khoản chi phí × mọi model cùng nhà cung cấp → bảng ước tính."""
    prices = prices or load_prices()
    out = []
    for it in items:
        cur = prices.get(it.get("model") or "")
        if not cur or it.get("usd") is None:
            continue
        for model, p in prices.items():
            if p.get("provider") != cur.get("provider"):
                continue
            usd, exact = reprice(it, model, prices)
            if usd is None:
                continue
            out.append({"role": it.get("role"), "from_model": it["model"], "model": model, "usd": usd,
                        "delta_usd": usd - it["usd"], "exact": exact, "current": model == it["model"]})
    return out
