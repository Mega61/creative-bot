"""Output formats. The format drives aspect ratio, safe zones, prompt guidance and export size."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    aspect_ratio: str
    export_size: tuple[int, int]
    # Fractions of the height covered by the app UI (profile bar, reply box, captions).
    safe_top: float
    safe_bottom: float
    guidance: str


FORMATS = {
    "post": Format(
        key="post",
        label="Post 4:5",
        aspect_ratio="4:5",
        export_size=(1080, 1350),
        safe_top=0.0,
        safe_bottom=0.0,
        guidance=(
            "Instagram feed post, vertical 4:5. The image is seen small in the feed first, "
            "so the subject must read clearly at thumbnail size. Balanced, editorial "
            "composition with breathing room around the subject."
        ),
    ),
    "story": Format(
        key="story",
        label="Story / Reel / TikTok 9:16",
        aspect_ratio="9:16",
        export_size=(1080, 1920),
        safe_top=0.13,
        safe_bottom=0.20,
        guidance=(
            "Full-screen vertical 9:16 for Instagram Stories, Reels covers and TikTok. "
            "Fill the whole frame edge to edge, bold and simple. The top 13% and bottom 20% "
            "of the frame are covered by the app interface: keep the subject, faces, "
            "products, logos and any text inside the middle area, and keep those top and "
            "bottom bands as calm background only."
        ),
    ),
    "square": Format(
        key="square",
        label="Square 1:1",
        aspect_ratio="1:1",
        export_size=(1080, 1080),
        safe_top=0.0,
        safe_bottom=0.0,
        guidance="Square 1:1 image (carousel slide or grid post). Centred, balanced composition.",
    ),
}
