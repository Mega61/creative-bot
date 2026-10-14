"""Runtime settings, read from the environment (and a local .env file)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def _ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


def _chat_brands(raw: str) -> dict[int, str]:
    # "-1001234:golden,-1005678:cassis"
    out = {}
    for pair in raw.replace(" ", "").split(","):
        if pair:
            chat, brand = pair.rsplit(":", 1)
            out[int(chat)] = brand
    return out


@dataclass
class Settings:
    gemini_api_key: str
    image_model: str = "gemini-nano-banana-2.1"
    text_model: str = "gemini-3.8-flash"
    draft_size: str = "2K"  # 1K | 2K | 4K
    proposals: int = 2
    brands_dir: Path = ROOT / "brands"
    data_dir: Path = ROOT / "data"
    upscale_weights: Path = ROOT / "models" / "RealESRGAN_x2plus.pth"
    upscale_tile: int = 512
    telegram_token: str = ""
    allowed_user_ids: set[int] = field(default_factory=set)
    chat_brands: dict[int, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(ROOT / ".env")
        env = os.environ.get
        s = cls(gemini_api_key=env("GEMINI_API_KEY", ""))
        s.image_model = env("IMAGE_MODEL", s.image_model)
        s.text_model = env("TEXT_MODEL", s.text_model)
        s.draft_size = env("DRAFT_SIZE", s.draft_size)
        s.proposals = int(env("PROPOSALS", s.proposals))
        s.brands_dir = Path(env("BRANDS_DIR", s.brands_dir))
        s.data_dir = Path(env("DATA_DIR", s.data_dir))
        s.upscale_weights = Path(env("UPSCALE_WEIGHTS", s.upscale_weights))
        s.upscale_tile = int(env("UPSCALE_TILE", s.upscale_tile))
        s.telegram_token = env("TELEGRAM_TOKEN", "")
        s.allowed_user_ids = _ids(env("ALLOWED_USER_IDS", ""))
        s.chat_brands = _chat_brands(env("CHAT_BRANDS", ""))
        return s
