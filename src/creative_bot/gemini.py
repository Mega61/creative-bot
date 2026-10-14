"""Thin wrapper over the Gemini Interactions API: one text model (prompt refiner) and one
image model (Nano Banana). A single GEMINI_API_KEY covers both."""

import base64
import logging

from google import genai
from pydantic import BaseModel

log = logging.getLogger(__name__)


class GenerationError(RuntimeError):
    pass


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

    async def structured[T: BaseModel](self, system: str, parts: list[dict], schema: type[T]) -> T:
        interaction = await self.client.aio.interactions.create(
            model=self.text_model,
            system_instruction=system,
            input=parts,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema.model_json_schema(),
            },
            store=False,
        )
        return schema.model_validate_json(interaction.output_text)

    async def image(self, prompt: str, images: list[bytes], aspect_ratio: str, size: str) -> bytes:
        parts = [text_part(prompt)] + [image_part(b) for b in images]
        interaction = await self.client.aio.interactions.create(
            model=self.image_model,
            input=parts,
            response_format={"type": "image", "aspect_ratio": aspect_ratio, "image_size": size},
            store=False,
        )
        out = interaction.output_image
        if not out or not out.data:
            # Usually a safety block or the model answering in text instead of drawing.
            raise GenerationError(interaction.output_text or "the model returned no image")
        return base64.b64decode(out.data)
