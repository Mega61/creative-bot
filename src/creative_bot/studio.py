"""The pipeline both front-ends (Telegram bot and CLI) drive."""

import asyncio
import io
import logging
from pathlib import Path

from PIL import Image

from .brand import Brand, load_brand
from .config import Settings
from .formats import FORMATS
from .gemini import Gemini, image_part, text_part
from .imaging import (
    Upscaler,
    normalize_reference,
    stamp_corner_logo,
    thumbnail_bytes,
    to_png_bytes,
)
from .refiner import RefinedPrompt, request_text, system_instruction
from .session import Round, Session

log = logging.getLogger(__name__)

TELEGRAM_DOC_LIMIT = 45 * 1024 * 1024  # bot uploads cap at 50 MB


class Studio:
    def __init__(self, settings: Settings, gemini: Gemini | None = None):
        self.settings = settings
        self._gemini = gemini
        self._brands: dict[str, Brand] = {}
        self.upscaler = Upscaler(settings.upscale_weights, settings.upscale_tile)

    @property
    def gemini(self) -> Gemini:
        if self._gemini is None:
            s = self.settings
            self._gemini = Gemini(s.gemini_api_key, s.image_model, s.text_model)
        return self._gemini

    def brand(self, brand_id: str) -> Brand:
        if brand_id not in self._brands:
            self._brands[brand_id] = load_brand(self.settings.brands_dir, brand_id)
        return self._brands[brand_id]

    def load_session(self, brand_id: str, sid: str) -> Session:
        return Session.load(self.settings.data_dir, brand_id, sid)

    # --- session lifecycle -------------------------------------------------------------

    def new_session(self, brand_id: str, fmt: str, brief: str, refs: list[bytes], user: str) -> Session:
        if fmt not in FORMATS:
            raise ValueError(f"unknown format {fmt!r}; use one of {', '.join(FORMATS)}")
        self.brand(brand_id)  # fail early on a bad brand id
        s = Session.create(self.settings.data_dir, brand_id, fmt, brief, user)
        for i, data in enumerate(refs, 1):
            rel = f"refs/ref-{i}.png"
            normalize_reference(data).save(s.path(rel))
            s.refs.append(rel)
        s.save()
        return s

    async def first_round(self, s: Session, model: str | None = None) -> Round:
        refined = await self._refine(s)
        return await self._generate(s, refined, model=model)

    async def revise(
        self, s: Session, feedback: str, edit_label: str | None = None, model: str | None = None
    ) -> Round:
        """Feedback round. With edit_label ('r02-b') the model edits that proposal instead of
        re-generating from the original references."""
        refined = await self._refine(s, feedback=feedback, edit_label=edit_label)
        return await self._generate(s, refined, feedback=feedback, edit_label=edit_label, model=model)

    async def retry(self, s: Session, model: str | None = None) -> Round:
        """Same prompt, fresh proposals (optionally with another model, to compare)."""
        last = s.last
        refined = RefinedPrompt(
            prompt=last.prompt,
            uses_logo_reference=last.uses_logo_reference,
            change_summary="Mismo prompt, nuevas propuestas.",
        )
        return await self._generate(s, refined, edit_label=last.edit_of, model=model)

    def undo(self, s: Session) -> Round | None:
        if len(s.rounds) > 1:
            s.rounds.pop()
            s.save()
        return s.last

    async def finalize(self, s: Session, image_label: str, corner_logo: bool = False) -> dict[str, Path]:
        """Deliverables for the approved proposal: the original exactly as generated, and an
        upscaled copy of that same picture. Optionally both again with the corner logo."""
        src = s.image_path(image_label)
        out_dir = s.path(f"final/{image_label}")
        out_dir.mkdir(parents=True, exist_ok=True)

        original = Image.open(src).convert("RGB")
        upscaled = await asyncio.to_thread(self.upscaler.upscale, original)

        files = {"original": _save(original, out_dir / "original"), "upscaled": _save(upscaled, out_dir / "upscaled")}
        if corner_logo:
            brand, fmt = self.brand(s.brand), FORMATS[s.format]
            files["original+logo"] = _save(stamp_corner_logo(original, brand, fmt), out_dir / "original-logo")
            files["upscaled+logo"] = _save(stamp_corner_logo(upscaled, brand, fmt), out_dir / "upscaled-logo")

        s.finals[image_label] = {
            "files": {k: str(p.relative_to(s.dir)) for k, p in files.items()},
            "upscale_method": self.upscaler.method,
            "size": f"{original.width}x{original.height} -> {upscaled.width}x{upscaled.height}",
        }
        s.save()
        return files

    # --- internals ---------------------------------------------------------------------

    def _edit_source(self, s: Session, edit_label: str | None) -> Path | None:
        return s.image_path(edit_label) if edit_label else None

    async def _refine(self, s: Session, feedback: str | None = None, edit_label: str | None = None) -> RefinedPrompt:
        brand, fmt = self.brand(s.brand), FORMATS[s.format]
        edit_src = self._edit_source(s, edit_label)
        text = request_text(
            brief=s.brief,
            ref_count=len(s.refs),
            previous_prompt=s.last.prompt if s.last else None,
            feedback=feedback,
            edit=edit_src is not None,
        )
        parts = [text_part(text)]
        # The refiner sees the same images, in the same order, as the image model will.
        if edit_src:
            parts.append(image_part(thumbnail_bytes(edit_src), "image/jpeg"))
        parts += [image_part(thumbnail_bytes(s.path(r)), "image/jpeg") for r in s.refs]
        return await self.gemini.structured(system_instruction(brand, fmt), parts, RefinedPrompt)

    async def _generate(
        self,
        s: Session,
        refined: RefinedPrompt,
        feedback: str | None = None,
        edit_label: str | None = None,
        model: str | None = None,
    ) -> Round:
        brand, fmt = self.brand(s.brand), FORMATS[s.format]
        model = model or self.settings.image_model
        images = []
        if edit_src := self._edit_source(s, edit_label):
            images.append(edit_src.read_bytes())
        # Always the original references, never earlier generations (except the edit target).
        images += [s.path(r).read_bytes() for r in s.refs]
        if refined.uses_logo_reference:
            images.append(to_png_bytes(Image.open(brand.primary_logo.file)))

        # Count folders, not live rounds: a round dropped by /undo keeps its folder.
        n = len(list(s.path("rounds").glob("r*"))) + 1 if s.path("rounds").exists() else 1
        rnd = Round(
            n=n,
            prompt=refined.prompt,
            change_summary=refined.change_summary,
            uses_logo_reference=refined.uses_logo_reference,
            feedback=feedback,
            edit_of=edit_label,
            model=model,
        )
        round_dir = s.path(f"rounds/r{n:02d}")
        round_dir.mkdir(parents=True, exist_ok=True)
        (round_dir / "prompt.txt").write_text(refined.prompt)

        results = await asyncio.gather(
            *(
                self.gemini.image(refined.prompt, images, fmt.aspect_ratio, self.settings.draft_size, model)
                for _ in range(self.settings.proposals)
            ),
            return_exceptions=True,
        )
        for i, res in enumerate(results):
            if isinstance(res, BaseException):
                log.warning("proposal %d failed: %s", i, res)
                rnd.errors.append(str(res))
                continue
            rel = f"rounds/r{n:02d}/{'abcdefgh'[i]}.png"
            Image.open(io.BytesIO(res)).save(s.path(rel))
            rnd.images.append(rel)
        s.images_generated += len(rnd.images)
        if not rnd.images:
            s.save()
            raise RuntimeError("no proposals were generated: " + "; ".join(rnd.errors))
        s.rounds.append(rnd)
        s.save()
        return rnd


def _save(img: Image.Image, stem: Path) -> Path:
    """PNG when it fits in a Telegram document, otherwise a high-quality JPEG."""
    png = stem.with_suffix(".png")
    img.save(png, optimize=False)
    if png.stat().st_size <= TELEGRAM_DOC_LIMIT:
        return png
    jpg = stem.with_suffix(".jpg")
    img.save(jpg, quality=95, subsampling=0)
    png.unlink()
    return jpg
