"""API /admin/* của công cụ dữ liệu. Mọi route đi qua dependency require_admin (rag_api.py)."""
from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from services.lawdata import phapdien, staging, store

MAX_UPLOAD = 40 * 1024 * 1024
MAX_PHAPDIEN_UPLOAD = 300 * 1024 * 1024


class StagingPatch(BaseModel):
    so_hieu: Optional[str] = None
    loai: Optional[str] = None
    trich_yeu: Optional[str] = None
    co_quan: Optional[str] = None
    ngay_ban_hanh: Optional[str] = None
    hieu_luc_tu: Optional[str] = None
    is_vbhn: Optional[bool] = None
    goc_so_hieu: Optional[str] = None
    demuc_id: Optional[str] = None
    chude_id: Optional[str] = None
    relations: Optional[list[dict]] = None
    new_relations: Optional[list[dict]] = None


class VanbanStatus(BaseModel):
    so_hieu: str
    trang_thai: str
    hieu_luc_den: str = ""
    thay_the_boi: str = ""


def _err(e: Exception) -> HTTPException:
    if isinstance(e, KeyError):
        return HTTPException(404, "Không tìm thấy mục")
    if isinstance(e, ValueError):
        return HTTPException(400, str(e))
    return HTTPException(500, f"Lỗi: {e}")


def build_router(require_admin: Callable[..., dict]) -> APIRouter:
    r = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

    @r.post("/ingest")
    async def ingest(files: list[UploadFile] = File(default=[]), urls: str = Form(default=""),
                     admin: dict = Depends(require_admin)) -> dict[str, Any]:
        """Nhiều văn bản một lúc: file PDF và/hoặc link (mỗi dòng một link)."""
        blobs = []
        for f in files:
            data = await f.read(MAX_UPLOAD + 1)
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, f"{f.filename}: lớn hơn 40MB")
            blobs.append((f.filename or "van-ban.pdf", data))
        try:
            items = staging.create_items(blobs, urls.splitlines(), admin["email"])
        except Exception as e:
            raise _err(e)
        return {"items": items}

    @r.get("/staging")
    def list_staging(status: str = "active", limit: int = 100) -> dict[str, Any]:
        return {"items": staging.list_items(status, min(max(limit, 1), 500))}

    @r.get("/staging/{iid}")
    def get_staging(iid: str) -> dict[str, Any]:
        try:
            return staging.get_item(iid)
        except Exception as e:
            raise _err(e)

    @r.patch("/staging/{iid}")
    def patch_staging(iid: str, body: StagingPatch) -> dict[str, Any]:
        try:
            return staging.update_item(iid, body.model_dump(exclude_none=True))
        except Exception as e:
            raise _err(e)

    @r.post("/staging/{iid}/approve")
    def approve(iid: str, admin: dict = Depends(require_admin)) -> dict[str, Any]:
        try:
            staging.approve(iid, admin["email"])
        except Exception as e:
            raise _err(e)
        return {"ok": True, "status": "publishing"}

    @r.post("/staging/{iid}/reject")
    def reject(iid: str) -> dict[str, Any]:
        try:
            staging.reject(iid)
        except Exception as e:
            raise _err(e)
        return {"ok": True}

    @r.post("/staging/{iid}/retry")
    def retry(iid: str) -> dict[str, Any]:
        try:
            staging.retry(iid)
        except Exception as e:
            raise _err(e)
        return {"ok": True}

    @r.get("/vanban")
    def vanban(q: str = "", limit: int = 100) -> dict[str, Any]:
        return {"items": store.list_vanban(q.strip(), min(max(limit, 1), 500))}

    @r.post("/vanban/status")
    def vanban_status(body: VanbanStatus) -> dict[str, Any]:
        try:
            store.set_vanban_status(body.so_hieu.strip(), body.trang_thai, body.hieu_luc_den, body.thay_the_boi.strip())
        except Exception as e:
            raise _err(e)
        return {"ok": True}

    @r.get("/demuc")
    def demuc() -> dict[str, Any]:
        return {"items": store.list_demuc()}

    @r.post("/phapdien")
    async def phapdien_upload(file: UploadFile = File(...), full: bool = Form(default=False),
                              admin: dict = Depends(require_admin)) -> dict[str, Any]:
        data = await file.read(MAX_PHAPDIEN_UPLOAD + 1)
        if len(data) > MAX_PHAPDIEN_UPLOAD:
            raise HTTPException(413, "File lớn hơn 300MB")
        try:
            return phapdien.create(data, file.filename or "phapdien.json", full, admin["email"])
        except Exception as e:
            raise _err(e)

    return r
