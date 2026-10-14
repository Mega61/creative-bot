"""Offline pipeline test: a fake Gemini stands in for the API, everything else is real."""

import asyncio
import io

import pytest
from PIL import Image

from creative_bot.config import Settings
from creative_bot.refiner import RefinedPrompt
from creative_bot.session import Session
from creative_bot.studio import Studio


class FakeGemini:
    def __init__(self, fail_first_image=False):
        self.image_calls = []
        self.refine_calls = []
        self.fail_first_image = fail_first_image

    async def structured(self, system, parts, schema):
        self.refine_calls.append((system, parts))
        return RefinedPrompt(prompt=f"prompt v{len(self.refine_calls)}", uses_logo_reference=True, change_summary="ok")

    async def image(self, prompt, images, aspect_ratio, size, model=None):
        self.image_calls.append((prompt, len(images), aspect_ratio, size))
        if self.fail_first_image and len(self.image_calls) == 1:
            raise RuntimeError("blocked")
        w, h = {"4:5": (400, 500), "9:16": (360, 640), "1:1": (400, 400)}[aspect_ratio]
        buf = io.BytesIO()
        Image.new("RGB", (w, h), (30, 30, 30)).save(buf, "PNG")
        return buf.getvalue()


def _photo(rotated=False) -> bytes:
    img = Image.new("RGB", (300, 200), "white")
    buf = io.BytesIO()
    exif = Image.Exif()
    if rotated:
        exif[0x0112] = 6  # phone portrait photo stored sideways
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


@pytest.fixture
def studio(tmp_path):
    s = Settings.load()
    s.data_dir = tmp_path
    s.upscale_weights = tmp_path / "missing.pth"  # Lanczos: keeps the test fast
    return Studio(s, gemini=FakeGemini())


def test_full_flow(studio):
    s = studio.new_session("golden", "story", "brief", [_photo(rotated=True), _photo()], "test")
    assert Image.open(s.path(s.refs[0])).size == (200, 300)  # EXIF rotation applied

    r1 = asyncio.run(studio.first_round(s))
    assert Session.labels(r1) == ["r01-a", "r01-b"]
    _, n_images, aspect, size = studio.gemini.image_calls[0]
    assert (n_images, aspect, size) == (3, "9:16", "2K")  # 2 refs + logo

    r2 = asyncio.run(studio.revise(s, "más dorado", edit_label="r01-b"))
    assert r2.edit_of == "r01-b"
    assert studio.gemini.image_calls[-1][1] == 4  # edited proposal + 2 refs + logo
    assert "PREVIOUS PROMPT:\nprompt v1" in studio.gemini.refine_calls[-1][1][0]["text"]

    studio.undo(s)
    r3 = asyncio.run(studio.retry(s, model="gemini-3-pro-image"))
    assert r3.n == 3 and r3.prompt == r1.prompt  # undo kept r02's folder, numbering moves on
    assert (r1.model, r3.model) == ("gemini-nano-banana-2.1", "gemini-3-pro-image")

    files = asyncio.run(studio.finalize(s, "r03-a", corner_logo=True))
    assert set(files) == {"original", "upscaled", "original+logo", "upscaled+logo"}
    assert Image.open(files["upscaled"]).size == (720, 1280)

    files = asyncio.run(studio.finalize(s, "r03-b", corner_logo=False, upscale=False))
    assert set(files) == {"original"} and s.finals["r03-b"]["upscale_method"] is None

    reloaded = studio.load_session("golden", s.id)
    assert [r.n for r in reloaded.rounds] == [1, 3]
    assert reloaded.images_generated == 6


def test_partial_failure_keeps_labels(studio):
    studio._gemini = FakeGemini(fail_first_image=True)
    s = studio.new_session("cassis", "post", "brief", [], "test")
    rnd = asyncio.run(studio.first_round(s))
    assert Session.labels(rnd) == ["r01-b"] and rnd.errors == ["blocked"]
    assert s.image_path("r01-b").name == "b.png"


def test_upscale_any(studio):
    from creative_bot.studio import ImageTooLarge

    path, size = asyncio.run(studio.upscale_any(_photo(rotated=True)))
    assert Image.open(path).size == (400, 600) and size == "200x300 -> 400x600"

    big = io.BytesIO()
    Image.new("RGB", (4000, 3000)).save(big, "PNG")
    with pytest.raises(ImageTooLarge):
        asyncio.run(studio.upscale_any(big.getvalue()))


def test_corner_logo_stays_out_of_story_ui(studio):
    from creative_bot.formats import FORMATS
    from creative_bot.imaging import stamp_corner_logo

    dark = Image.new("RGB", (1080, 1920), (10, 10, 10))
    out = stamp_corner_logo(dark, studio.brand("golden"), FORMATS["story"])
    changed = Image.eval(out.convert("L"), lambda v: 255 if v > 15 else 0).getbbox()
    assert changed[3] <= 1920 * 0.80  # above the bottom 20% covered by the app UI
