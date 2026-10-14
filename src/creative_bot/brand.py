"""Brand kits: one folder per tenant under brands/<id>/ with a brand.yaml and logo files."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Logo:
    key: str
    file: Path
    light: Path | None
    description: str


@dataclass
class Brand:
    id: str
    name: str
    language: str
    raw: dict
    logos: dict[str, Logo]
    corner_stamp: dict

    @property
    def primary_logo(self) -> Logo:
        return self.logos.get("primary") or next(iter(self.logos.values()))

    def art_direction(self) -> str:
        """The brand kit as plain text for the prompt refiner."""
        r = self.raw
        lines = [f"BRAND: {self.name}", f"About: {r.get('about', '').strip()}"]
        if r.get("audience"):
            lines.append(f"Audience: {r['audience'].strip()}")
        if r.get("visual_style"):
            lines.append("Visual style:\n" + "\n".join(f"- {s}" for s in r["visual_style"]))
        if r.get("palette"):
            lines.append("Palette: " + ", ".join(f"{k} {v}" for k, v in r["palette"].items()))
        if r.get("typography"):
            lines.append("Typography: " + "; ".join(f"{k}: {v}" for k, v in r["typography"].items()))
        if r.get("always"):
            lines.append("Always:\n" + "\n".join(f"- {s}" for s in r["always"]))
        if r.get("never"):
            lines.append("Never:\n" + "\n".join(f"- {s}" for s in r["never"]))
        lines.append(f"Language for any on-image text: {self.language}")
        return "\n\n".join(lines)


def load_brand(brands_dir: Path, brand_id: str) -> Brand:
    root = brands_dir / brand_id
    raw = yaml.safe_load((root / "brand.yaml").read_text())
    logos = {}
    for key, spec in (raw.get("logos") or {}).items():
        logos[key] = Logo(
            key=key,
            file=root / spec["file"],
            light=root / spec["light"] if spec.get("light") else None,
            description=" ".join(spec.get("description", "").split()),
        )
    return Brand(
        id=raw["id"],
        name=raw["name"],
        language=raw.get("language", "es"),
        raw=raw,
        logos=logos,
        corner_stamp=raw.get("corner_stamp") or {},
    )


def list_brands(brands_dir: Path) -> list[str]:
    return sorted(p.parent.name for p in brands_dir.glob("*/brand.yaml"))
