"""Telegram front-end. One Telegram group per brand; everyone in the allowlist can drive it.

Flow in a brand group:
  1. Send reference photo(s) with a caption (the brief), or /nuevo <brief> with no photos.
  2. Pick the format (post / story / square).
  3. The bot replies with 2 proposals A/B and buttons.
  4. Write feedback as a normal message -> new round from the original references.
     Reply to one proposal's photo with feedback -> the model edits that exact image.
  5. ✅ on a proposal -> original + upscaled files (optionally with the corner logo).
"""

import asyncio
import io
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup, InputMediaPhoto, Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .brand import list_brands
from .config import Settings
from .formats import FORMATS
from .gemini import IMAGE_MODELS, model_label
from .session import Round, Session
from .studio import Studio

log = logging.getLogger(__name__)

ALBUM_WAIT = 1.5  # seconds to wait for the rest of an album before acting on it


@dataclass
class Pending:
    """References and brief collected before the format is chosen."""

    refs: list[bytes] = field(default_factory=list)
    brief: str = ""
    user: str = ""


@dataclass
class ChatState:
    brand: str | None = None  # override set with /marca (otherwise from CHAT_BRANDS)
    session: str | None = None
    model: str | None = None  # image model picked with /modelo (otherwise IMAGE_MODEL)
    proposals: dict[str, str] = field(default_factory=dict)  # message_id -> "sid:r01-a"
    pending: Pending | None = None


class Bot:
    def __init__(self, settings: Settings):
        self.s = settings
        self.studio = Studio(settings)
        self.state_file = settings.data_dir / "chats.json"
        self.chats: dict[int, ChatState] = self._load_state()
        self.locks: dict[int, asyncio.Lock] = {}
        self.album_tasks: dict[int, asyncio.Task] = {}

    # --- state -------------------------------------------------------------------------

    def _load_state(self) -> dict[int, ChatState]:
        if not self.state_file.exists():
            return {}
        raw = json.loads(self.state_file.read_text())
        return {
            int(k): ChatState(v.get("brand"), v.get("session"), v.get("model"), v.get("proposals", {}))
            for k, v in raw.items()
        }

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            str(k): {"brand": v.brand, "session": v.session, "model": v.model, "proposals": v.proposals}
            for k, v in self.chats.items()
        }
        self.state_file.write_text(json.dumps(data, indent=2))

    def chat(self, chat_id: int) -> ChatState:
        return self.chats.setdefault(chat_id, ChatState())

    def brand_for(self, chat_id: int) -> str | None:
        return self.chat(chat_id).brand or self.s.chat_brands.get(chat_id)

    def model_for(self, chat_id: int) -> str:
        return self.chat(chat_id).model or self.s.image_model

    def session_for(self, chat_id: int, sid: str | None = None) -> Session | None:
        brand, sid = self.brand_for(chat_id), sid or self.chat(chat_id).session
        if not brand or not sid:
            return None
        return self.studio.load_session(brand, sid)

    def lock(self, chat_id: int) -> asyncio.Lock:
        return self.locks.setdefault(chat_id, asyncio.Lock())

    # --- guards ------------------------------------------------------------------------

    async def allowed(self, update: Update) -> bool:
        user = update.effective_user
        if user and user.id in self.s.allowed_user_ids:
            return True
        log.info("ignored update from user %s in chat %s", user and user.id, update.effective_chat.id)
        return False

    async def require_brand(self, update: Update) -> str | None:
        brand = self.brand_for(update.effective_chat.id)
        if not brand:
            await update.effective_message.reply_text(
                "Este chat no tiene marca asignada. Usa /marca <" + "|".join(list_brands(self.s.brands_dir)) + ">"
            )
        return brand

    # --- commands ----------------------------------------------------------------------

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.allowed(update):
            return
        brand = self.brand_for(update.effective_chat.id) or "ninguna"
        model = model_label(self.model_for(update.effective_chat.id))
        await update.effective_message.reply_text(
            f"Marca de este chat: {brand} · Modelo: {model}\n\n"
            "1. Envía foto(s) de referencia con las instrucciones en el texto de la foto.\n"
            "   (Mejor calidad: envíalas como archivo, no como foto.)\n"
            "   Sin fotos: /nuevo <instrucciones>\n"
            "2. Elige el formato: post, historia o cuadrado.\n"
            "3. Te mando 2 propuestas (A y B).\n"
            "4. Escribe qué cambiar → nueva ronda desde las fotos originales.\n"
            "   Responde a la foto A o B con el cambio → edito esa imagen exacta.\n"
            "5. ✅ en la que te guste → original + versión escalada.\n\n"
            "/prompt ver el prompt actual · /deshacer volver una ronda · /otra repetir\n"
            "/modelo cambiar el modelo de imagen (aplica desde la siguiente ronda)\n"
            "/marca <id> cambiar marca · /chatid ver el id de este chat"
        )

    async def cmd_chatid(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # Answered for anyone: needed once to fill CHAT_BRANDS and ALLOWED_USER_IDS.
        await update.effective_message.reply_text(
            f"chat_id: {update.effective_chat.id}\nuser_id: {update.effective_user.id}"
        )

    async def cmd_brand(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.allowed(update):
            return
        brands = list_brands(self.s.brands_dir)
        if not ctx.args or ctx.args[0] not in brands:
            await update.effective_message.reply_text("Uso: /marca <" + "|".join(brands) + ">")
            return
        st = self.chat(update.effective_chat.id)
        st.brand, st.session, st.proposals = ctx.args[0], None, {}
        self._save_state()
        await update.effective_message.reply_text(f"Marca: {self.studio.brand(st.brand).name}")

    async def cmd_model(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.allowed(update):
            return
        current = self.model_for(update.effective_chat.id)
        buttons = [
            [Btn(("● " if m == current else "") + label, callback_data=f"model:{m}")]
            for m, label in IMAGE_MODELS.items()
        ]
        await update.effective_message.reply_text(
            "Modelo de imagen (precio por imagen a 2K; cada ronda son 2):",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def cmd_new(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.allowed(update) or not await self.require_brand(update):
            return
        st = self.chat(update.effective_chat.id)
        st.pending = Pending(brief=" ".join(ctx.args), user=update.effective_user.first_name)
        if st.pending.brief:
            await self.ask_format(update.effective_message)
        else:
            await update.effective_message.reply_text("¿Qué quieres crear? Escribe las instrucciones.")

    async def cmd_prompt(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.allowed(update):
            return
        s = self.session_for(update.effective_chat.id)
        if not s or not s.last:
            await update.effective_message.reply_text("No hay una sesión activa.")
            return
        await update.effective_message.reply_text(f"Ronda {s.last.n}:\n\n{s.last.prompt}"[:4000])

    async def cmd_undo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.allowed(update):
            return
        s = self.session_for(update.effective_chat.id)
        if not s or len(s.rounds) < 2:
            await update.effective_message.reply_text("No hay ronda anterior a la que volver.")
            return
        rnd = self.studio.undo(s)
        await update.effective_message.reply_text(
            f"Volví a la ronda {rnd.n}. El próximo cambio parte de ese prompt.\n{rnd.change_summary}"
        )

    async def cmd_retry(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.allowed(update):
            return
        await self.run_round(update.effective_message, update.effective_chat.id, mode="retry")

    # --- incoming photos and text ------------------------------------------------------

    async def on_image(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.allowed(update) or not await self.require_brand(update):
            return
        msg = update.effective_message
        chat_id = update.effective_chat.id
        st = self.chat(chat_id)
        if st.pending is None or (not msg.media_group_id and st.pending.refs and st.pending.brief):
            st.pending = Pending(user=update.effective_user.first_name)
        file = await (msg.document or msg.photo[-1]).get_file()
        st.pending.refs.append(bytes(await file.download_as_bytearray()))
        if msg.caption:
            st.pending.brief = (st.pending.brief + "\n" + msg.caption).strip()

        # Albums arrive as one message per photo; act once the last one is in.
        if task := self.album_tasks.get(chat_id):
            task.cancel()
        self.album_tasks[chat_id] = asyncio.create_task(self._after_album(msg, chat_id))

    async def _after_album(self, msg: Message, chat_id: int) -> None:
        await asyncio.sleep(ALBUM_WAIT)
        pending = self.chat(chat_id).pending
        if not pending:
            return
        if pending.brief:
            await self.ask_format(msg)
        else:
            await msg.reply_text(f"Tengo {len(pending.refs)} foto(s). ¿Qué quieres crear con ellas?")

    async def on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.allowed(update) or not await self.require_brand(update):
            return
        msg = update.effective_message
        chat_id = update.effective_chat.id
        st = self.chat(chat_id)

        if st.pending is not None and not st.pending.brief:
            st.pending.brief = msg.text
            await self.ask_format(msg)
            return
        if not st.session:
            await msg.reply_text("Envía foto(s) con instrucciones, o /nuevo <instrucciones>.")
            return

        edit_label = None
        if msg.reply_to_message and (ref := st.proposals.get(str(msg.reply_to_message.message_id))):
            sid, edit_label = ref.split(":")
            if sid != st.session:
                await msg.reply_text("Esa imagen es de una sesión anterior; respondo sobre la actual.")
                edit_label = None
        await self.run_round(msg, chat_id, mode="revise", feedback=msg.text, edit_label=edit_label)

    async def ask_format(self, msg: Message) -> None:
        buttons = [[Btn(f.label, callback_data=f"fmt:{f.key}")] for f in FORMATS.values()]
        model = model_label(self.model_for(msg.chat_id))
        await msg.reply_text(
            f"¿Para qué formato? (modelo: {model} · /modelo para cambiar)",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    # --- buttons -----------------------------------------------------------------------

    async def on_button(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not await self.allowed(update):
            await q.answer("No autorizado")
            return
        await q.answer()
        chat_id = update.effective_chat.id
        action, _, arg = q.data.partition(":")

        if action == "model":
            st = self.chat(chat_id)
            st.model = arg
            self._save_state()
            await q.edit_message_text(
                f"Modelo: {model_label(arg)}. Aplica desde la siguiente ronda; "
                "🔁 Otra vez repite el mismo prompt con este modelo."
            )
        elif action == "fmt":
            await q.edit_message_reply_markup(None)
            await self.start_session(q.message, chat_id, arg)
        elif action == "retry":
            await self.run_round(q.message, chat_id, mode="retry")
        elif action == "prompt":
            await self.cmd_prompt(update, ctx)
        elif action == "pick":  # pick:<sid>:<label>
            sid, image_label = arg.split(":")
            buttons = [
                [Btn("Sin logo", callback_data=f"fin:{sid}:{image_label}:0")],
                [Btn("Con logo en la esquina", callback_data=f"fin:{sid}:{image_label}:1")],
            ]
            await q.message.reply_text(
                f"Elegida {image_label.upper()}. ¿Agrego el logo en la esquina?",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        elif action == "fin":
            sid, image_label, logo = arg.split(":")
            await q.edit_message_reply_markup(None)
            await self.finalize(q.message, chat_id, sid, image_label, logo == "1")

    # --- work --------------------------------------------------------------------------

    async def start_session(self, msg: Message, chat_id: int, fmt: str) -> None:
        st = self.chat(chat_id)
        pending, st.pending = st.pending, None
        if not pending or not pending.brief:
            await msg.reply_text("Se perdió la solicitud. Vuelve a enviar las fotos con las instrucciones.")
            return
        s = self.studio.new_session(self.brand_for(chat_id), fmt, pending.brief, pending.refs, pending.user)
        st.session, st.proposals = s.id, {}
        self._save_state()
        await self.run_round(msg, chat_id, mode="first")

    async def run_round(
        self,
        msg: Message,
        chat_id: int,
        mode: str,
        feedback: str | None = None,
        edit_label: str | None = None,
    ) -> None:
        lock = self.lock(chat_id)
        if lock.locked():
            await msg.reply_text("Todavía estoy trabajando en la ronda anterior, un momento…")
            return
        async with lock:
            s = self.session_for(chat_id)
            if not s:
                await msg.reply_text("No hay una sesión activa.")
                return
            if mode == "retry" and not s.last:
                mode = "first"
            note = {
                "first": "Afinando el prompt y generando 2 propuestas…",
                "retry": "Mismo prompt, 2 propuestas nuevas…",
                "revise": f"Editando {edit_label.upper()}…" if edit_label else "Aplicando el cambio…",
            }[mode]
            await msg.reply_text(note)
            try:
                async with _typing(msg, ChatAction.UPLOAD_PHOTO):
                    model = self.model_for(chat_id)
                    if mode == "first":
                        rnd = await self.studio.first_round(s, model)
                    elif mode == "retry":
                        rnd = await self.studio.retry(s, model)
                    else:
                        rnd = await self.studio.revise(s, feedback, edit_label, model)
            except Exception as e:  # report and keep the session usable
                log.exception("round failed")
                await msg.reply_text(f"Falló la generación: {str(e)[:500]}\nPrueba /otra o reformula.")
                return
            await self.send_round(msg, chat_id, s, rnd)

    async def send_round(self, msg: Message, chat_id: int, s: Session, rnd: Round) -> None:
        labels = Session.labels(rnd)
        media = [
            InputMediaPhoto(_preview(s.path(rel)), caption=lab[-1].upper())
            for rel, lab in zip(rnd.images, labels)
        ]
        sent = await msg.chat.send_media_group(media)
        st = self.chat(chat_id)
        for m, lab in zip(sent, labels):
            st.proposals[str(m.message_id)] = f"{s.id}:{lab}"
        self._save_state()

        pick_row = [Btn(f"✅ {lab[-1].upper()}", callback_data=f"pick:{s.id}:{lab}") for lab in labels]
        tools = [Btn("🔁 Otra vez", callback_data="retry"), Btn("📝 Prompt", callback_data="prompt")]
        text = f"<b>Ronda {rnd.n}</b> · {model_label(rnd.model or self.s.image_model)}\n{_escape(rnd.change_summary)}"
        if rnd.errors:
            text += f"\n({len(rnd.errors)} propuesta(s) fallaron)"
        text += "\n\nEscribe qué cambiar, o responde a una foto para editar esa."
        await msg.chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([pick_row, tools]))

    async def finalize(self, msg: Message, chat_id: int, sid: str, image_label: str, logo: bool) -> None:
        lock = self.lock(chat_id)
        async with lock:
            s = self.session_for(chat_id, sid)
            await msg.reply_text("Preparando los archivos finales (escalado x2)…")
            try:
                async with _typing(msg, ChatAction.UPLOAD_DOCUMENT):
                    files = await self.studio.finalize(s, image_label, corner_logo=logo)
            except Exception as e:
                log.exception("finalize failed")
                await msg.reply_text(f"Falló el final: {str(e)[:500]}")
                return
            info = s.finals[image_label]
            captions = {
                "original": "Original (tal cual se generó)",
                "upscaled": f"Escalada ({info['upscale_method']})",
                "original+logo": "Original + logo",
                "upscaled+logo": f"Escalada + logo ({info['upscale_method']})",
            }
            for key, path in files.items():
                with open(path, "rb") as f:
                    await msg.chat.send_document(
                        f, filename=f"{s.brand}-{s.id}-{image_label}-{path.name}", caption=captions[key]
                    )
            await msg.chat.send_message(f"Listo · {info['size']}. Para empezar otra: envía fotos o /nuevo.")


def _preview(path: Path) -> bytes:
    """Telegram photo previews: JPEG under the 10 MB photo limit."""
    img = Image.open(path).convert("RGB")
    img.thumbnail((2560, 2560), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _typing:
    """Keep the 'sending photo…' indicator alive during long calls."""

    def __init__(self, msg: Message, action: str):
        self.msg, self.action, self.task = msg, action, None

    async def _loop(self):
        while True:
            await self.msg.chat.send_action(self.action)
            await asyncio.sleep(4.5)

    async def __aenter__(self):
        self.task = asyncio.create_task(self._loop())

    async def __aexit__(self, *exc):
        self.task.cancel()


def run(settings: Settings) -> None:
    if not settings.telegram_token:
        raise SystemExit("TELEGRAM_TOKEN is not set")
    if not settings.allowed_user_ids:
        log.warning("ALLOWED_USER_IDS is empty: the bot will only answer /chatid")
    bot = Bot(settings)
    async def set_commands(app: Application) -> None:
        # The "/" menu in Telegram.
        await app.bot.set_my_commands(
            [
                ("ayuda", "Cómo usar el bot"),
                ("nuevo", "Empezar sin fotos: /nuevo <instrucciones>"),
                ("modelo", "Cambiar el modelo de imagen"),
                ("otra", "Repetir con el mismo prompt"),
                ("deshacer", "Volver a la ronda anterior"),
                ("prompt", "Ver el prompt actual"),
                ("chatid", "Ver ids de este chat y tuyo"),
            ]
        )

    app = (
        Application.builder()
        .token(settings.telegram_token)
        .concurrent_updates(True)
        .post_init(set_commands)
        .build()
    )
    app.add_handler(CommandHandler(["start", "ayuda", "help"], bot.cmd_help))
    app.add_handler(CommandHandler("chatid", bot.cmd_chatid))
    app.add_handler(CommandHandler("marca", bot.cmd_brand))
    app.add_handler(CommandHandler("nuevo", bot.cmd_new))
    app.add_handler(CommandHandler("modelo", bot.cmd_model))
    app.add_handler(CommandHandler("prompt", bot.cmd_prompt))
    app.add_handler(CommandHandler("deshacer", bot.cmd_undo))
    app.add_handler(CommandHandler("otra", bot.cmd_retry))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, bot.on_image))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.on_text))
    app.add_handler(CallbackQueryHandler(bot.on_button))
    app.run_polling(allowed_updates=Update.ALL_TYPES)
