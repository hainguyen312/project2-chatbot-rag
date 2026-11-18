"""
Ước tính chi phí một lần chạy TRƯỚC khi chạy — từ lần chạy gần nhất có ghi chi phí, không gọi API.

  chi phí ≈ Σ_khoản  giá(model mới cho vai trò đó) × token đã dùng (hoặc quy đổi từ số tiền)
            × tỉ lệ quy mô (repeat, n … so với lần chạy làm mốc)
"""
from __future__ import annotations

from typing import Any, Optional

from . import pricing, store
from .suites import SUITES_BY_ID, Suite, coerce_params


def _basis_param(suite: Suite, run: dict, name: str) -> Any:
    for src in (run.get("params") or {}, run.get("config") or {}):
        if src.get(name) not in (None, ""):
            return src[name]
    return next((p.default for p in suite.params if p.name == name), None)


def estimate(suite_id: str, params: dict) -> dict:
    suite = SUITES_BY_ID.get(suite_id)
    if not suite:
        raise ValueError("Bộ đánh giá không tồn tại")
    clean = coerce_params(suite, params)
    if not suite.model_params and not suite.scale_params:
        return {"usd": 0.0, "exact": True, "basis": None, "items": [], "note": "Không gọi API có phí."}

    role_model = {role: clean.get(param) for param, role in suite.model_params.items() if clean.get(param)}

    # Mốc: ưu tiên lần chạy gần nhất CÙNG model (model khác thường gọi tool / sinh token khác nhau,
    # nên quy đổi giá chỉ là xấp xỉ hành vi); không có thì lấy lần gần nhất có ghi chi phí.
    basis: Optional[dict] = None
    same_model = False
    for r in store.list_runs(suite_id):
        if r["status"] != "done":
            continue
        d = store.get_run(r["id"])
        items = (d.get("cost") or {}).get("items") or []
        if not items:
            continue
        match = all(role_model.get(i.get("role"), i.get("model")) == i.get("model") for i in items)
        if match:
            basis, same_model = d, True
            break
        if basis is None:
            basis = d
    if basis is None:
        hint = " Dùng tuỳ chọn dry-run để ước tính bằng đếm token (miễn phí)." if suite_id == "retrieval" else ""
        return {"usd": None, "exact": False, "basis": None, "items": [],
                "note": "Chưa có lần chạy nào ghi chi phí để làm mốc." + hint}

    # Quy mô: repeat / n … mới so với lần mốc
    scale, scale_note = 1.0, []
    for name in suite.scale_params:
        new, old = clean.get(name), _basis_param(suite, basis, name)
        if isinstance(new, (int, float)) and isinstance(old, (int, float)) and old:
            scale *= new / old
            if new != old:
                scale_note.append(f"{name} {old} → {new}")

    prices = pricing.load_prices()
    items, total, exact = [], 0.0, True
    for it in basis["cost"]["items"]:
        target = role_model.get(it.get("role")) or it.get("model")
        usd, ok = pricing.reprice(it, target, prices)
        if usd is None:
            return {"usd": None, "exact": False, "basis": _basis_info(basis), "items": [],
                    "note": f"Chưa có giá cho model {target} hoặc {it.get('model')} — thêm vào bảng giá."}
        usd *= scale
        exact = exact and ok
        total += usd
        items.append({"role": it.get("role"), "from_model": it.get("model"), "model": target, "usd": usd, "exact": ok})

    notes = []
    if not same_model:
        notes.append("Chưa có lần chạy nào với model này — quy đổi giá từ model khác; số token thật có thể khác "
                     "vì mỗi model gọi tool / trả lời dài ngắn khác nhau.")
    if scale_note:
        notes.append("Quy mô: " + ", ".join(scale_note) + ".")
    if suite_id == "retrieval":
        notes.append("Judge có cache: cặp đã chấm không tốn thêm, nên chi phí thật thường thấp hơn.")
    if basis["cost"].get("source") == "cost_only" and not same_model:
        notes.append("Mốc chỉ ghi số tiền — quy đổi theo tỉ lệ giá.")
    return {"usd": total, "exact": exact, "same_model": same_model, "basis": _basis_info(basis), "items": items,
            "note": " ".join(notes)}


def _basis_info(run: dict) -> dict:
    c = run.get("cost") or {}
    return {"id": run["id"], "created_at": run.get("created_at"), "usd": c.get("total_usd"),
            "models": sorted({i.get("model") for i in c.get("items") or [] if i.get("model")})}
