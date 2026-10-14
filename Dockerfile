FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
# No uv cache in the image (it doubled the size). Triton is only for torch.compile; unused.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 PYTHONUNBUFFERED=1

WORKDIR /app
# The ~3 GB dependency layer (torch + CUDA). Rebuilt only when pyproject/uv.lock change.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --extra upscale --no-install-package triton --no-install-project

ADD https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth models/RealESRGAN_x2plus.pth

# Code and brand kits: plain COPY, no RUN after this point. A RUN here would force CI to
# download the 3 GB layer above on every build; with only COPY, a code change is a few MB.
COPY src ./src
COPY brands ./brands
ENV PATH=/app/.venv/bin:$PATH PYTHONPATH=/app/src DATA_DIR=/data

VOLUME /data
# GPU: the stack reserves the NVIDIA device; without one the upscaler runs on CPU.
CMD ["python", "-m", "creative_bot", "bot"]
