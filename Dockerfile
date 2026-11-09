# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \
    PIP_RETRIES=10 \
    PIP_DEFAULT_TIMEOUT=120 \
    APP_ENV=production \
    PORT=5001 \
    APS_DATA_DIR=/data \
    HF_HOME=/app/model-cache \
    HF_ENDPOINT=https://hf-mirror.com \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# torch / onnxruntime 在 slim 镜像中需要的运行库
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# 优先安装 CPU 版 torch，避免默认镜像带入庞大的 CUDA 依赖。
RUN pip install --upgrade pip \
    && (pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.5.1" \
        || pip install "torch==2.5.1") \
    && pip install -r requirements.txt

# 把嵌入模型预下载进镜像；如网络受限可用 --build-arg PRELOAD_MODEL=false 跳过。
ARG PRELOAD_MODEL=true
RUN mkdir -p /app/model-cache /data \
    && if [ "$PRELOAD_MODEL" = "true" ]; then \
         mirror.com python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5')"; \
       fi

COPY . .

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
    CMD curl -fsS http://127.0.0.1:5001/api/health || exit 1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
