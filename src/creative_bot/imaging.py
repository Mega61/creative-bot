"""Local image work: input normalisation, corner logo stamp, upscaling. No API calls, no cost."""

import io
import logging
from pathlib import Path

from PIL import Image, ImageOps, ImageStat

from .brand import Brand
from .formats import Format

log = logging.getLogger(__name__)

MAX_REF_EDGE = 2048  # references larger than this add upload time, not quality


def normalize_reference(data: bytes, max_edge: int | None = MAX_REF_EDGE) -> Image.Image:
    """Apply the EXIF rotation to the pixels (phone photos), drop the tag, cap the size."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
    if max_edge:
        img.thumbnail((max_edge, max_edge), Image.LANCZOS)
    return img


def to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def thumbnail_bytes(path: Path, edge: int = 1024) -> bytes:
    """Small JPEG copy so the prompt refiner can look at a reference cheaply."""
    img = Image.open(path).convert("RGB")
    img.thumbnail((edge, edge), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


# --- corner logo -------------------------------------------------------------------------


def stamp_corner_logo(img: Image.Image, brand: Brand, fmt: Format) -> Image.Image:
    """Overlay the brand's real logo file in a corner. For the 'small mark in the corner'
    use case only; logos that are part of the scene are drawn by the image model."""
    spec = brand.corner_stamp
    logo_def = brand.logos.get(spec.get("logo", "primary")) or brand.primary_logo
    position = spec.get("position", "bottom-right")
    width_pct = float(spec.get("width_pct", 20)) / 100
    margin_pct = float(spec.get("margin_pct", 4)) / 100

    base = img.convert("RGBA")
    W, H = base.size
    logo = Image.open(logo_def.file).convert("RGBA")
    lw = round(W * width_pct)
    lh = round(logo.height * lw / logo.width)

    margin = round(W * margin_pct)
    top_min = round(H * fmt.safe_top) + margin
    bottom_max = H - round(H * fmt.safe_bottom) - margin
    x = {"left": margin, "right": W - lw - margin, "center": (W - lw) // 2}[
        position.split("-")[1] if "-" in position else "center"
    ]
    y = top_min if position.startswith("top") else bottom_max - lh

    # Pick the light variant when the area under the logo is dark.
    region = base.crop((x, y, x + lw, y + lh)).convert("L")
    if logo_def.light and ImageStat.Stat(region).mean[0] < 110:
        logo = Image.open(logo_def.light).convert("RGBA")

    logo = logo.resize((lw, lh), Image.LANCZOS)
    base.alpha_composite(logo, (x, y))
    return base.convert("RGB")


# --- upscaling ---------------------------------------------------------------------------


class Upscaler:
    """Real-ESRGAN x2 via spandrel, on the GPU when there is one (seconds) or the CPU (minutes).
    Same picture, more pixels: no re-generation, no drift.

    Falls back to plain Lanczos when torch/spandrel or the weights are missing, and says so
    through `method` so the deliverable is never labelled as something it isn't.
    """

    def __init__(self, weights: Path, tile: int = 512):
        self.weights = weights
        self.tile = tile
        self._model = None
        self.device = "cpu"
        self.method = "lanczos"

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            import spandrel
            import torch
        except ImportError:
            log.warning("torch/spandrel not installed; upscaling falls back to Lanczos")
            return None
        if not self.weights.exists():
            log.warning("upscale weights %s missing; falling back to Lanczos", self.weights)
            return None
        self._model = spandrel.ModelLoader().load_from_file(str(self.weights)).eval()
        if torch.cuda.is_available():
            self.device = "cuda"
            self._model.to("cuda")
            log.info("upscaler on GPU: %s", torch.cuda.get_device_name(0))
        else:
            log.info("upscaler on CPU (no CUDA device visible)")
        self.method = f"esrgan-x{self._model.scale}-{self.device}"
        return self._model

    def upscale(self, img: Image.Image) -> Image.Image:
        model = self._load()
        img = img.convert("RGB")
        if model is None:
            return img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
        return self._run_tiled(model, img)

    def _run_tiled(self, model, img: Image.Image) -> Image.Image:
        import numpy as np
        import torch

        scale, tile, pad = model.scale, self.tile, 16
        src = torch.from_numpy(np.array(img)).permute(2, 0, 1).float().div(255).unsqueeze(0)
        _, _, h, w = src.shape
        out = torch.zeros((1, 3, h * scale, w * scale))
        with torch.inference_mode():
            for y in range(0, h, tile):
                for x in range(0, w, tile):
                    # Process each tile with some context around it, then keep only the core,
                    # so tile seams don't show.
                    y0, x0 = max(y - pad, 0), max(x - pad, 0)
                    y1, x1 = min(y + tile + pad, h), min(x + tile + pad, w)
                    res = model(src[:, :, y0:y1, x0:x1].to(self.device)).cpu()
                    cy, cx = (y - y0) * scale, (x - x0) * scale
                    th, tw = (min(y + tile, h) - y) * scale, (min(x + tile, w) - x) * scale
                    out[:, :, y * scale : y * scale + th, x * scale : x * scale + tw] = res[
                        :, :, cy : cy + th, cx : cx + tw
                    ]
        if self.device == "cuda":
            torch.cuda.empty_cache()  # hand the VRAM back between finals
        arr = out.squeeze(0).clamp(0, 1).mul(255).round().byte().permute(1, 2, 0).numpy()
        return Image.fromarray(arr)
