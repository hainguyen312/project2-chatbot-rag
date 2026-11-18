"""
Chạy suite đánh giá dưới dạng tiến trình con, có hàng đợi và giới hạn số lần chạy đồng thời.

Mặc định chạy ngay trong tiến trình backend (thread + subprocess) — đủ cho một máy.
Khi scale: thay `LocalRunner` bằng worker riêng (RQ / Celery / Cloud Run job…) đọc cùng định dạng
meta; API và UI không phải đổi vì chỉ đọc meta + file kết quả qua store.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from typing import Optional

from . import store
from .suites import ROOT, SUITES_BY_ID, coerce_params

MAX_CONCURRENT = max(1, int(os.getenv("EVAL_MAX_CONCURRENT", "1")))
ACTIVE = ("queued", "running")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class LocalRunner:
    def __init__(self, max_concurrent: int = MAX_CONCURRENT):
        self.max_concurrent = max_concurrent
        self._lock = threading.Lock()
        self._queue: deque[str] = deque()
        self._procs: dict[str, subprocess.Popen] = {}
        self._cancelled: set[str] = set()

    # -------------------------------------------------------------- public
    def submit(self, suite_id: str, params: dict, started_by: str) -> dict:
        suite = SUITES_BY_ID.get(suite_id)
        if not suite:
            raise ValueError("Bộ đánh giá không tồn tại")
        clean = coerce_params(suite, params)
        with self._lock:
            run_id = self._new_id(suite.file_prefix, clean)
            meta = {"id": run_id, "suite": suite_id, "status": "queued", "params": clean,
                    "started_by": started_by, "created_at": _now(), "started_at": None,
                    "finished_at": None, "error": None, "exit_code": None}
            store.save_meta(meta)
            self._queue.append(run_id)
        self._pump()
        return meta

    def cancel(self, run_id: str) -> dict:
        meta = store.load_meta(run_id)
        if not meta:
            raise KeyError(run_id)
        if meta["status"] not in ACTIVE:
            raise ValueError("Lần chạy đã kết thúc")
        with self._lock:
            self._cancelled.add(run_id)
            if run_id in self._queue:
                self._queue.remove(run_id)
                meta.update(status="cancelled", finished_at=_now())
                store.save_meta(meta)
                return meta
            proc = self._procs.get(run_id)
        if proc:
            proc.terminate()
        return {**meta, "status": "cancelling"}

    def recover(self) -> int:
        """Khởi động lại backend: lần chạy đang dở (không còn tiến trình) → interrupted."""
        n = 0
        for m in store.all_metas().values():
            if m.get("status") in ACTIVE:
                m.update(status="interrupted", finished_at=_now(),
                         error="Backend khởi động lại khi lần chạy chưa xong")
                store.save_meta(m)
                n += 1
        return n

    # -------------------------------------------------------------- nội bộ
    def _new_id(self, prefix: str, params: dict) -> str:
        model = params.get("model")
        base = f"{prefix}{model}_" if prefix == "tool_eval_" and model else prefix
        while True:
            rid = base + time.strftime("%Y%m%d_%H%M%S")
            if not os.path.exists(store.result_path(rid)) and not os.path.exists(store.meta_path(rid)):
                return rid
            time.sleep(1)

    def _pump(self) -> None:
        with self._lock:
            while self._queue and len(self._procs) < self.max_concurrent:
                run_id = self._queue.popleft()
                self._procs[run_id] = None  # type: ignore[assignment]  # giữ chỗ
                threading.Thread(target=self._run, args=(run_id,), daemon=True, name=f"eval-{run_id}").start()

    def _run(self, run_id: str) -> None:
        meta = store.load_meta(run_id) or {}
        suite = SUITES_BY_ID[meta["suite"]]
        out = store.result_path(run_id)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        argv = suite.build_argv(meta.get("params") or {}, out)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        meta.update(status="running", started_at=_now(), argv=[os.path.basename(argv[0]), *argv[1:]])
        store.save_meta(meta)
        code: Optional[int] = None
        err: Optional[str] = None
        try:
            with open(store.log_path(run_id), "w", encoding="utf-8") as log:
                log.write("$ " + " ".join(meta["argv"]) + "\n\n")
                log.flush()
                proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL)
                with self._lock:
                    self._procs[run_id] = proc
                    cancelled_early = run_id in self._cancelled
                if cancelled_early:
                    proc.terminate()
                code = proc.wait()
            if suite.postprocess and os.path.exists(out + ".raw"):
                suite.postprocess(out + ".raw", out)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        finally:
            with self._lock:
                self._procs.pop(run_id, None)
                cancelled = run_id in self._cancelled
                self._cancelled.discard(run_id)
            has_result = os.path.exists(out)
            if cancelled:
                status = "cancelled"
            elif has_result:
                status = "done"  # pytest trả mã ≠ 0 khi có test trượt — vẫn là một kết quả
            else:
                status = "failed"
                err = err or self._tail_error(run_id, code)
            meta.update(status=status, finished_at=_now(), exit_code=code, error=err)
            store.save_meta(meta)
            self._pump()

    @staticmethod
    def _tail_error(run_id: str, code: Optional[int]) -> str:
        tail = [ln.strip() for ln in store.read_log(run_id, 30).splitlines() if ln.strip()]
        last = tail[-1] if tail else ""
        return f"Không có file kết quả (mã thoát {code}). {last}"[:500]


runner = LocalRunner()
