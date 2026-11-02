"""Bot command routing, with Telegram objects mocked out."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from creative_bot.bot import Bot, Pending
from creative_bot.config import Settings

CHAT = -100
USER = 1


@pytest.fixture
def bot(tmp_path):
    s = Settings.load()
    s.data_dir = tmp_path
    s.allowed_user_ids = {USER}
    b = Bot(s)
    b.upscale_and_send = AsyncMock()
    return b


def _update():
    u = MagicMock()
    u.effective_user.id = USER
    u.effective_chat.id = CHAT
    u.effective_message.reply_to_message = None
    u.effective_message.reply_text = AsyncMock()
    return u


def test_upscale_after_photos_without_brief(bot):
    bot.chat(CHAT).pending = Pending(refs=[b"one", b"two"])
    u = _update()
    asyncio.run(bot.cmd_upscale(u, MagicMock()))
    assert [c.args[1] for c in bot.upscale_and_send.call_args_list] == [b"one", b"two"]
    assert bot.chat(CHAT).pending is None  # no creative flow left waiting for a brief
    assert not bot.chat(CHAT).awaiting_upscale


def test_upscale_alone_waits_for_the_image(bot):
    u = _update()
    asyncio.run(bot.cmd_upscale(u, MagicMock()))
    bot.upscale_and_send.assert_not_called()
    assert bot.chat(CHAT).awaiting_upscale


def test_upscale_leaves_a_briefed_flow_alone(bot):
    bot.chat(CHAT).pending = Pending(refs=[b"one"], brief="navidad")
    asyncio.run(bot.cmd_upscale(_update(), MagicMock()))
    bot.upscale_and_send.assert_not_called()
    assert bot.chat(CHAT).pending.brief == "navidad"
