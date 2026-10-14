FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --extra upscale --no-install-project

ADD https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth models/RealESRGAN_x2plus.pth

COPY src ./src
COPY brands ./brands
RUN uv sync --frozen --no-dev --extra upscale

ENV DATA_DIR=/data
VOLUME /data
CMD ["uv", "run", "--no-sync", "creative-bot", "bot"]
