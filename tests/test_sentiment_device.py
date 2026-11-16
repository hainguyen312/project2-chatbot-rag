"""
Chọn device cho SentimentAgent: cuda nếu có GPU, fallback cpu, ghi log "device: …".

Không tải model thật (2.1 GB): AutoTokenizer/AutoModel được thay bằng bản giả.
Test cuối chỉ chạy khi máy có GPU (trong container GPU) để xác nhận cuda dùng được thật.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")


def _import_sentiment_agent():
    """Import agents.sentiment_agent KHÔNG chạy agents/__init__.py.

    agents/__init__ → pipeline.py tạo AgentsManager() ở cấp module → tải model 2.1 GB.
    Tạm dùng package rỗng trỏ cùng thư mục, import xong thì gỡ để test khác import
    `agents` bình thường (module con đã nạp được tái sử dụng).
    """
    if "agents.sentiment_agent" in sys.modules:
        return sys.modules["agents.sentiment_agent"]
    real_pkg = sys.modules.get("agents")
    stub = types.ModuleType("agents")
    stub.__path__ = [str(Path(__file__).resolve().parents[1] / "agents")]
    sys.modules["agents"] = stub
    try:
        return importlib.import_module("agents.sentiment_agent")
    finally:
        if real_pkg is not None:
            sys.modules["agents"] = real_pkg
        else:
            del sys.modules["agents"]


sa = _import_sentiment_agent()


@pytest.fixture
def cuda(monkeypatch):
    """Giả lập có/không có GPU: cuda(True) / cuda(False)."""
    def _set(available: bool):
        monkeypatch.setattr(sa.torch.cuda, "is_available", lambda: available)
        monkeypatch.setattr(sa.torch.cuda, "get_device_name", lambda i=0: "Fake GPU")
    return _set


@pytest.mark.parametrize(
    "pref, has_gpu, expected",
    [
        ("auto", True, "cuda"),
        ("auto", False, "cpu"),
        ("cuda", True, "cuda"),
        ("cuda", False, "cpu"),   # fallback khi không có GPU
        ("cpu", True, "cpu"),     # ép cpu dù có GPU
        ("CUDA ", True, "cuda"),  # không phân biệt hoa thường, bỏ khoảng trắng
        ("", True, "cuda"),       # rỗng → auto
        ("tpu", True, "cuda"),    # giá trị lạ → auto
        ("tpu", False, "cpu"),
    ],
)
def test_select_device(cuda, pref, has_gpu, expected):
    cuda(has_gpu)
    assert sa.select_device(pref) == expected


def test_select_device_reads_env(cuda, monkeypatch):
    cuda(True)
    monkeypatch.setenv("SENTIMENT_DEVICE", "cpu")
    assert sa.select_device() == "cpu"
    monkeypatch.delenv("SENTIMENT_DEVICE")
    assert sa.select_device() == "cuda"  # mặc định auto


# ── SentimentAgent với model giả ─────────────────────────────────────────────
class _FakeEnc(dict):
    def __init__(self, n: int):
        super().__init__(input_ids=torch.zeros((n, 4), dtype=torch.long))
        self.device = None

    def to(self, device):
        self.device = device
        return self


class _FakeTokenizer:
    def __init__(self):
        self.last: _FakeEnc | None = None

    def __call__(self, premise, hyp, **kw):
        self.last = _FakeEnc(len(hyp))
        return self.last


class _FakeModel:
    def __init__(self):
        self.device = None

    def to(self, device):
        self.device = device
        return self

    def eval(self):
        return self

    def __call__(self, input_ids, **kw):
        n = input_ids.shape[0]
        logits = torch.zeros((n, 3))
        logits[0, 2] = 5.0  # nhãn đầu (neutral_info) entailment cao
        return type("Out", (), {"logits": logits})()


@pytest.fixture
def fake_hf(monkeypatch):
    tok, model = _FakeTokenizer(), _FakeModel()
    monkeypatch.setattr(sa.AutoTokenizer, "from_pretrained", classmethod(lambda cls, *a, **k: tok))
    monkeypatch.setattr(
        sa.AutoModelForSequenceClassification, "from_pretrained", classmethod(lambda cls, *a, **k: model)
    )
    return tok, model


@pytest.mark.parametrize("has_gpu, expected", [(True, "cuda"), (False, "cpu")])
def test_agent_moves_model_and_inputs_to_device(cuda, fake_hf, monkeypatch, capsys, has_gpu, expected):
    monkeypatch.delenv("SENTIMENT_DEVICE", raising=False)
    cuda(has_gpu)
    tok, model = fake_hf

    agent = sa.SentimentAgent()

    assert agent.device == expected
    assert model.device == expected
    out = capsys.readouterr().out
    assert f"[Sentiment] device: {expected}" in out
    if has_gpu:
        assert "Fake GPU" in out

    res = agent.run("Cho tôi hỏi điều kiện kết hôn")
    assert tok.last.device == expected
    assert res["is_findinfo"] is True
    assert res["is_negative"] is False


@pytest.mark.skipif(not torch.cuda.is_available(), reason="cần GPU thật")
def test_real_cuda_selected_and_usable(monkeypatch):
    monkeypatch.delenv("SENTIMENT_DEVICE", raising=False)
    assert sa.select_device() == "cuda"
    x = torch.ones((64, 64), device="cuda")
    assert float((x @ x).sum()) == 64.0 * 64 * 64
