"""Command line front-end: same pipeline as the bot, handy for testing quality without Telegram.

  creative-bot bot
  creative-bot new golden post "brief..." --ref photo.jpg --ref product.png
  creative-bot revise golden <session> "feedback" [--edit r01-b]
  creative-bot retry golden <session>
  creative-bot final golden <session> r01-a [--logo]
  creative-bot upscale image.png        # try the upscaler alone
  creative-bot stamp golden post image.png  # try the corner logo alone
"""

import argparse
import asyncio
import logging
from pathlib import Path

from PIL import Image

from .config import Settings
from .formats import FORMATS
from .session import Round, Session
from .studio import Studio


def _print_round(s: Session, rnd: Round) -> None:
    print(f"\nsession {s.id} · round {rnd.n} · {rnd.change_summary}")
    for rel, lab in zip(rnd.images, Session.labels(rnd)):
        print(f"  {lab}: {s.path(rel)}")
    for err in rnd.errors:
        print(f"  failed: {err}")
    print(f"\n--- prompt ---\n{rnd.prompt}\n")


def main() -> None:
    p = argparse.ArgumentParser(prog="creative-bot")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bot")

    new = sub.add_parser("new")
    new.add_argument("brand")
    new.add_argument("format", choices=list(FORMATS))
    new.add_argument("brief")
    new.add_argument("--ref", action="append", default=[], type=Path)

    rev = sub.add_parser("revise")
    rev.add_argument("brand")
    rev.add_argument("session")
    rev.add_argument("feedback")
    rev.add_argument("--edit", help="proposal to edit, e.g. r01-b")

    retry = sub.add_parser("retry")
    retry.add_argument("brand")
    retry.add_argument("session")

    fin = sub.add_parser("final")
    fin.add_argument("brand")
    fin.add_argument("session")
    fin.add_argument("label")
    fin.add_argument("--logo", action="store_true", help="also add the corner logo")

    up = sub.add_parser("upscale")
    up.add_argument("image", type=Path)

    st = sub.add_parser("stamp")
    st.add_argument("brand")
    st.add_argument("format", choices=list(FORMATS))
    st.add_argument("image", type=Path)

    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings.load()

    if args.cmd == "bot":
        from .bot import run

        run(settings)
        return

    studio = Studio(settings)

    if args.cmd == "new":
        s = studio.new_session(args.brand, args.format, args.brief, [r.read_bytes() for r in args.ref], "cli")
        _print_round(s, asyncio.run(studio.first_round(s)))
    elif args.cmd == "revise":
        s = studio.load_session(args.brand, args.session)
        _print_round(s, asyncio.run(studio.revise(s, args.feedback, args.edit)))
    elif args.cmd == "retry":
        s = studio.load_session(args.brand, args.session)
        _print_round(s, asyncio.run(studio.retry(s)))
    elif args.cmd == "final":
        s = studio.load_session(args.brand, args.session)
        files = asyncio.run(studio.finalize(s, args.label, args.logo))
        print(s.finals[args.label]["upscale_method"], s.finals[args.label]["size"])
        for k, v in files.items():
            print(f"  {k}: {v}")
    elif args.cmd == "upscale":
        out = studio.upscaler.upscale(Image.open(args.image))
        dest = args.image.with_name(f"{args.image.stem}-up-{studio.upscaler.method}.png")
        out.save(dest)
        print(dest, out.size)
    elif args.cmd == "stamp":
        from .imaging import stamp_corner_logo

        out = stamp_corner_logo(Image.open(args.image), studio.brand(args.brand), FORMATS[args.format])
        dest = args.image.with_name(f"{args.image.stem}-logo.png")
        out.save(dest)
        print(dest)
