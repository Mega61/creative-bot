"""A session is one piece of content: brief + references -> rounds of proposals -> final.

Everything is plain files so a session can be inspected (or rescued) by hand:

    data/<brand>/<session-id>/
        session.json
        refs/ref-1.png ...
        rounds/r01/prompt.txt, a.png, b.png
        final/r01-a/original.png, upscaled.png, ...

/undo drops the last round from session.json; its files stay on disk.
"""

import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Round:
    n: int
    prompt: str
    change_summary: str
    uses_logo_reference: bool
    feedback: str | None = None
    edit_of: str | None = None  # "r02-b" when this round edits a chosen proposal
    model: str | None = None  # image model used for this round
    images: list[str] = field(default_factory=list)  # paths relative to the session dir
    errors: list[str] = field(default_factory=list)


@dataclass
class Session:
    id: str
    brand: str
    format: str
    brief: str
    created_by: str
    created_at: str
    refs: list[str] = field(default_factory=list)
    rounds: list[Round] = field(default_factory=list)
    finals: dict[str, dict] = field(default_factory=dict)
    images_generated: int = 0
    dir: Path | None = field(default=None, repr=False)

    @property
    def last(self) -> Round | None:
        return self.rounds[-1] if self.rounds else None

    def path(self, rel: str) -> Path:
        assert self.dir is not None
        return self.dir / rel

    def image_path(self, image_label: str) -> Path:
        """'r02-b' -> the file of proposal b in round 2."""
        rn, letter = image_label.lower().lstrip("r").split("-")
        rnd = next(r for r in self.rounds if r.n == int(rn))
        rel = next(p for p in rnd.images if Path(p).stem == letter)
        return self.path(rel)

    @staticmethod
    def labels(rnd: Round) -> list[str]:
        return [f"r{rnd.n:02d}-{Path(p).stem}" for p in rnd.images]

    def save(self) -> None:
        data = asdict(self)
        data.pop("dir")
        self.path("session.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))

    @classmethod
    def create(cls, data_dir: Path, brand: str, fmt: str, brief: str, user: str) -> "Session":
        now = datetime.now()
        sid = now.strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(2)
        s = cls(id=sid, brand=brand, format=fmt, brief=brief, created_by=user, created_at=now.isoformat())
        s.dir = data_dir / brand / sid
        (s.dir / "refs").mkdir(parents=True)
        s.save()
        return s

    @classmethod
    def load(cls, data_dir: Path, brand: str, sid: str) -> "Session":
        d = data_dir / brand / sid
        raw = json.loads((d / "session.json").read_text())
        raw["rounds"] = [Round(**r) for r in raw["rounds"]]
        s = cls(**raw)
        s.dir = d
        return s
