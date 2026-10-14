# creative-bot

Telegram bot that turns a reference photo and a short brief into on-brand images for
Instagram and TikTok. It refines the brief into a proper prompt, generates 2 proposals with
Nano Banana, lets you iterate, and hands back the approved image both as-is and upscaled.

Multi-brand: one brand kit per business under `brands/`, and one Telegram group per brand.
Today: `golden` (Golden Beauty Studio) and `cassis` (Cassis Originals).

Supersedes the single-brand draft in `../cassis-originals-social-media/creative-bot-plan.md`.

## How it's used (in the brand's Telegram group)

1. Send one or more reference photos, and write the brief as the photo caption.
   Send them **as a file** for best quality (Telegram compresses normal photos).
   No reference? `/nuevo <brief>`.
2. Pick the format: **Post 4:5**, **Story / Reel / TikTok 9:16** or **Square 1:1**.
3. The bot replies with proposals **A** and **B**.
4. Iterate:
   - Write a normal message ("más dorado, quita la planta") → a new round. The prompt
     changes only what you asked, and the image is generated again from the **original**
     photos.
   - **Reply to photo A or B** with the change → the model edits that exact image. Use this
     when one is almost right.
   - 🔁 Otra vez = same prompt, new pair. `/deshacer` = go back one round. 📝 Prompt = see it.
5. ✅ on the one you like → one tap: with/without the corner logo, with/without upscaling:
   - `original` — exactly the image you approved
   - `upscaled` (if chosen) — the same picture at 2× (Real-ESRGAN on the GPU). Sharper edges
     and text, but it can smooth fine textures. Compare both and post whichever looks better.
   - `+logo` versions, if you asked for the corner logo.

`/upscale` works on any image, at any time: reply `/upscale` to it (a proposal, a photo, a
file), send an image with `/upscale` as the caption, or send `/upscale` and then the image.
Up to 3072 px per side; send it as a file for the best result.

## Two ways a logo ends up in an image

| Use case | Who draws it | How |
|---|---|---|
| Logo is **part of the scene** (printed on the bottle, a sign, packaging, embroidery) | Nano Banana | Say so in the brief. The refiner attaches the logo file as a reference and describes it exactly. |
| Small **mark in a corner** | Code | Choose "Con logo en la esquina" at the end. The real logo file is stamped on (light variant on dark backgrounds, kept clear of the Story UI). |

## Formats

| Format | Aspect | Notes |
|---|---|---|
| post | 4:5 | Instagram feed. Must read at thumbnail size. |
| story | 9:16 | Stories, Reels covers, TikTok. Top 13% and bottom 20% stay free of subject/text/logo (app UI). |
| square | 1:1 | Carousels, grid. |

Defined in `src/creative_bot/formats.py`. The format changes the aspect ratio, the prompt
guidance and where the corner logo goes.

## Models (one Gemini API key for everything)

- Prompt refiner: `gemini-3.8-flash` (`TEXT_MODEL`). It looks at the reference photos and
  applies the brand kit.
- Images: `gemini-nano-banana-2.1` (`IMAGE_MODEL`), at `DRAFT_SIZE=2K`. Use `gemini-3-pro-image`
  for Nano Banana Pro.
- Upscale: Real-ESRGAN x2 on CPU, local and free. To try another model, point
  `UPSCALE_WEIGHTS` at any [spandrel](https://github.com/chaiNNer-org/spandrel)-supported file.

## Brand kits

```
brands/<id>/
  brand.yaml   # about, audience, visual style, palette, fonts, always/never, logos, corner stamp
  logos/       # transparent PNGs (+ light variants for dark backgrounds)
```

To add a brand: copy a folder, edit `brand.yaml`, drop the logos in, create a Telegram
group, and add `<chat_id>:<id>` to `CHAT_BRANDS`.

## Setup

1. **Bot:** create it with @BotFather. Then `/setprivacy` → *Disable*, so it can read
   feedback messages in groups.
2. **Groups:** create "Golden – Content" and "Cassis – Content". Add the bot and both of
   you. Send `/chatid` in each group to get the chat id and your user ids.
3. **Gemini key:** from https://aistudio.google.com/apikey. Billing must be enabled for
   image models.
4. Fill `.env` from `.env.example`.

### Run locally

```bash
uv sync --extra upscale
mkdir -p models && curl -L -o models/RealESRGAN_x2plus.pth \
  https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth
uv run creative-bot bot
```

### Run on the server (Portainer, Git mode)

Same pattern as the other stacks: pushing to `main` runs the tests, builds the image to
`ghcr.io/mega61/creative-bot`, and commits its digest into `deploy/stack.yml`. Portainer polls the
repo and redeploys. Rollback = `git revert` the deploy commit.

One-time setup in Portainer: **Stacks → Add stack → Repository**
- Repository: `https://github.com/Mega61/creative-bot`, reference `refs/heads/main`
- Compose path: `deploy/stack.yml`
- Authentication: none (public repo)
- GitOps updates: on, polling
- Environment variables: `GEMINI_API_KEY`, `TELEGRAM_TOKEN`, `ALLOWED_USER_IDS`, `CHAT_BRANDS`
- The image on GHCR is public: no registry credentials needed

No ports, networks, proxy or database needed. Sessions live in the `creative_bot_data` volume.
`docker-compose.local.yml` builds and runs the image locally from `.env`.

### CLI (same pipeline, no Telegram)

```bash
uv run creative-bot new golden post "brief..." --ref photo.jpg --ref product.png
uv run creative-bot revise golden <session> "más dorado" [--edit r01-b]
uv run creative-bot retry golden <session>
uv run creative-bot final golden <session> r01-a [--logo]
uv run creative-bot upscale image.png
uv run creative-bot stamp golden story image.png
uv run pytest
```

## Where things are saved

```
data/<brand>/<session>/
  session.json                 brief, format, every round's prompt, feedback, files
  refs/ref-1.png ...           originals (EXIF rotation applied)
  rounds/r01/prompt.txt a.png b.png
  final/r01-a/original.png upscaled.png [original-logo.png upscaled-logo.png]
```

## Not built yet

- Cost tracking and budget caps (the session counts images generated: `images_generated`)
- Saving winning prompts as reusable templates per brand
- Caption and hashtag suggestions
- Scheduling and publishing (an n8n or Meta API step on top of `final/`)
