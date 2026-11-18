# syntax=docker/dockerfile:1.7
# Backend FastAPI (rag_api:app, cổng 8001).
#
#   GPU (mặc định, máy chủ RTX 5060 Ti / Blackwell sm_120):
#     docker build -t chatbot-backend .
#   CPU (CI):
#     docker build --build-arg TORCH_VARIANT=cpu -t chatbot-backend:cpu .
#
# torch lấy từ index của PyTorch. cu128 là bản CUDA thấp nhất có sm_120; index cu128 chỉ có
# tới torch 2.11.0 → ghim 2.11.0 cho cả GPU lẫn CPU để hai bản giống nhau.
# Phần còn lại ghim bằng requirements.lock.txt (sinh từ image này, xem đầu file lock).
# Model HF (~2.1 GB) KHÔNG nằm trong image: tải lần đầu vào HF_HOME (volume) khi khởi động.
# Bí mật (.env, firebase-credentials.json) KHÔNG nằm trong image: compose truyền lúc chạy.

ARG PYTHON_IMAGE=python:3.12.16-slim-trixie
FROM ${PYTHON_IMAGE}

ARG TORCH_VERSION=2.11.0
ARG TORCH_VARIANT=cu128
ARG APP_UID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=10 \
    HF_HOME=/data/hf \
    HF_HUB_DISABLE_TELEMETRY=1 \
    BM25_CACHE_DIR=/data/bm25 \
    LAWDATA_DIR=/data/lawdata \
    EVAL_DATA_DIR=/data/eval \
    SENTIMENT_DEVICE=auto

RUN groupadd --gid ${APP_UID} app \
 && useradd --uid ${APP_UID} --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app

WORKDIR /app

# torch riêng một layer (nặng nhất, ~3 GB với cu128) để đổi requirements không phải tải lại.
# CHỈ dùng index PyTorch (đã có đủ phụ thuộc của torch): gói nvidia-* lấy từ CDN NVIDIA.
# Không thêm --extra-index-url PyPI — pip sẽ chọn bản trên files.pythonhosted.org, mà một số
# IP Fastly từ mạng máy chủ chỉ ~130 KB/s → build treo cả giờ (đo 16/11/2026).
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url "https://download.pytorch.org/whl/${TORCH_VARIANT}" \
                "torch==${TORCH_VERSION}+${TORCH_VARIANT}"

COPY requirements.txt requirements.lock.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt -c requirements.lock.txt \
 && python -c "import torch; v=torch.__version__; assert v.startswith('${TORCH_VERSION}+${TORCH_VARIANT}'), v"

# Code thuộc root, user app chỉ đọc. Thư mục dữ liệu ghi được → mount volume vào đây.
COPY . .
RUN mkdir -p /data/hf /data/bm25 /data/lawdata /data/eval \
 && chown -R app:app /data

USER app
EXPOSE 8001

# start-period dài: lần đầu phải tải model 2.1 GB và dựng BM25 (~1–2 phút).
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "rag_api:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--timeout-graceful-shutdown", "30"]
