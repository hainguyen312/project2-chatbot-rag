"""API /admin/eval/* — xem, chạy, so sánh các lần đánh giá. Mọi route đi qua require_admin."""
from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import pricing, store
from .estimate import estimate
from .runner import runner
from .suites import SUITES, SUITES_BY_ID


class RunCreate(BaseModel):
    suite: str
    params: dict[str, Any] = {}


class Pricing(BaseModel):
    models: dict[str, Optional[dict[str, Any]]]


class RunPatch(BaseModel):
    label: Optional[str] = None


def _err(e: Exception) -> HTTPException:
    if isinstance(e, KeyError):
        return HTTPException(404, "Không tìm thấy lần chạy")
    if isinstance(e, ValueError):
        return HTTPException(400, str(e))
    return HTTPException(500, f"Lỗi: {e}")


def build_router(require_admin: Callable[..., dict]) -> APIRouter:
    r = APIRouter(prefix="/admin/eval", dependencies=[Depends(require_admin)])

    @r.get("/suites")
    def suites() -> dict[str, Any]:
        return {"suites": [s.public() for s in SUITES], "max_concurrent": runner.max_concurrent}

    @r.get("/runs")
    def runs(suite: str = "", limit: int = 200) -> dict[str, Any]:
        if suite and suite not in SUITES_BY_ID:
            raise HTTPException(400, "Bộ đánh giá không tồn tại")
        return {"runs": store.list_runs(suite, min(max(limit, 1), 1000))}

    @r.post("/runs")
    def create(body: RunCreate, admin: dict = Depends(require_admin)) -> dict[str, Any]:
        try:
            return runner.submit(body.suite, body.params, admin.get("email", ""))
        except Exception as e:
            raise _err(e)

    @r.post("/estimate")
    def estimate_cost(body: RunCreate) -> dict[str, Any]:
        """Ước tính chi phí trước khi chạy (từ lần chạy cũ + bảng giá) — không gọi API có phí."""
        try:
            return estimate(body.suite, body.params)
        except Exception as e:
            raise _err(e)

    @r.get("/pricing")
    def get_pricing() -> dict[str, Any]:
        return {"models": pricing.load_prices(), "batch_discount": pricing.BATCH_DISCOUNT}

    @r.put("/pricing")
    def put_pricing(body: Pricing) -> dict[str, Any]:
        try:
            return {"models": pricing.save_prices(body.models), "batch_discount": pricing.BATCH_DISCOUNT}
        except Exception as e:
            raise _err(e)

    @r.get("/runs/{run_id}")
    def get(run_id: str) -> dict[str, Any]:
        try:
            return store.get_run(run_id)
        except Exception as e:
            raise _err(e)

    @r.patch("/runs/{run_id}")
    def patch(run_id: str, body: RunPatch) -> dict[str, Any]:
        try:
            store.update_label(run_id, body.label or "")
            return store.get_run(run_id)
        except Exception as e:
            raise _err(e)

    @r.get("/runs/{run_id}/log")
    def log(run_id: str, tail: int = 400) -> dict[str, Any]:
        try:
            meta = store.load_meta(run_id) if store.valid_id(run_id) else None
            return {"status": (meta or {}).get("status", "done"),
                    "text": store.read_log(run_id, min(max(tail, 1), 5000))}
        except Exception as e:
            raise _err(e)

    @r.post("/runs/{run_id}/cancel")
    def cancel(run_id: str) -> dict[str, Any]:
        try:
            return runner.cancel(run_id)
        except Exception as e:
            raise _err(e)

    return r
