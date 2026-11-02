"""Thin wrapper over the Gemini Interactions API: one text model (prompt refiner) and one
image model (Nano Banana). A single GEMINI_API_KEY covers both."""

import base64
import logging

import httpx
from google import genai
from pydantic import BaseModel

log = logging.getLogger(__name__)


# Image models the team can pick from in Telegram (/modelo). Prices per image, Standard tier,
# from ai.google.dev/gemini-api/docs/pricing (checked 2026-10-14).
IMAGE_MODELS = {
    "gemini-nano-banana-2.1": "Nano Banana 2.1 · $0.05",
    "gemini-3.1-flash-image": "Nano Banana 2 · $0.10",
    "gemini-3-pro-image": "Nano Banana Pro · $0.13",
}


def model_label(model: str) -> str:
    return IMAGE_MODELS.get(model, model).split(" · ")[0]


class GenerationError(RuntimeError):
    pass


# Uploads from the server sometimes stall mid-request (seen 2026-11-02, ~1 in 10 calls). With
# no timeout the SDK waited ~16 min before failing the round, with the chat locked meanwhile.
# Timeouts are per attempt, in seconds: refining takes ~5 s, a 2K image well under a minute.
# We retry ourselves because the SDK wraps transport errors before its own retry sees them.
TEXT_TIMEOUT = 60
IMAGE_TIMEOUT = 180
ATTEMPTS = 3


def image_part(data: bytes, mime: str = "image/png") -> dict:
    return {"type": "image", "data": base64.b64encode(data).decode(), "mime_type": mime}


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


class Gemini:
    def __init__(self, api_key: str, image_model: str, text_model: str):
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self.client = genai.Client(api_key=api_key)
        self.image_model = image_model
        self.text_model = text_model

    async def _create(self, timeout: float, **kwargs):
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return await self.client.aio.interactions.create(store=False, timeout=timeout, **kwargs)
            except Exception as e:
                # Network stalls and drops; API errors (4xx/5xx) are left to the SDK.
                if attempt == ATTEMPTS or not isinstance(e.__cause__, httpx.TransportError):
                    raise
                log.warning("gemini %s, retrying (%d/%d)", type(e.__cause__).__name__, attempt, ATTEMPTS)

    async def structured[T: BaseModel](self, system: str, parts: list[dict], schema: type[T]) -> T:
        interaction = await self._create(
            TEXT_TIMEOUT,
            model=self.text_model,
            system_instruction=system,
            input=parts,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema.model_json_schema(),
            },
        )
        return schema.model_validate_json(interaction.output_text)

    async def image(
        self, prompt: str, images: list[bytes], aspect_ratio: str, size: str, model: str | None = None
    ) -> bytes:
        parts = [text_part(prompt)] + [image_part(b) for b in images]
        interaction = await self._create(
            IMAGE_TIMEOUT,
            model=model or self.image_model,
            input=parts,
            response_format={"type": "image", "aspect_ratio": aspect_ratio, "image_size": size},
        )
        out = interaction.output_image
        if not out or not out.data:
            # Usually a safety block or the model answering in text instead of drawing.
            raise GenerationError(interaction.output_text or "the model returned no image")
        return base64.b64decode(out.data)
