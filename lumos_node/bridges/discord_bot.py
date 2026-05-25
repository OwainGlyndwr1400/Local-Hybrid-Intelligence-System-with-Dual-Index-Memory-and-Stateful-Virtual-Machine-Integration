"""Discord bridge: operator-only DM forwarder to the local /chat SSE endpoint.

Outbound websocket client only — no inbound network exposure. The bot connects to
Discord's gateway, listens for DMs from the configured operator user ID, forwards
each message to localhost:<port>/chat, and streams the response back via batched
Discord message-edits.

Config (.env):
    LUMOS_DISCORD_TOKEN          — bot token from discord.com/developers
    LUMOS_DISCORD_OPERATOR_ID    — your Discord user ID (Developer Mode → Copy ID)
    LUMOS_DISCORD_HOST           — defaults to settings.host (127.0.0.1)
    LUMOS_DISCORD_PORT           — defaults to settings.port (8765)
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import aiohttp
import discord
from discord import app_commands

from ..config import get_settings
from ..log import get_logger

log = get_logger(__name__)

DISCORD_MSG_MAX = 2000
EDIT_INTERVAL_SEC = 0.6  # batch progressive edits — Discord rate-limits ~5/sec
TYPING_REFRESH_SEC = 8.0  # refresh typing indicator every ~10s


class LumosDiscordBot(discord.Client):
    """Operator-only DM bridge with per-session memory continuity."""

    def __init__(self, operator_id: int, lumos_url: str) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        super().__init__(intents=intents)
        self.operator_id = operator_id
        self.lumos_url = lumos_url.rstrip("/")
        self.tree = app_commands.CommandTree(self)
        # Stable session_id across DMs — created on first message, persists in
        # memory until /reset or process restart. The Lumos server's
        # restore_history mechanism will rehydrate from JSONL if needed.
        self.session_id: str | None = None
        self._register_commands()

    # ---- Lifecycle ----------------------------------------------------------

    async def setup_hook(self) -> None:
        # Sync slash commands globally. May take up to an hour to propagate
        # for global commands — use guild-scoped sync for instant rollout in dev.
        await self.tree.sync()
        log.info("discord.commands_synced")

    async def on_ready(self) -> None:
        log.info(
            "discord.ready",
            user=str(self.user),
            operator_id=self.operator_id,
            lumos_url=self.lumos_url,
        )
        # Set a presence so the operator can tell at a glance the bot is online.
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.CustomActivity(name="Resonator · Extra Coil"),
        )

    # ---- Message handling ---------------------------------------------------

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.author.id != self.operator_id:
            return  # operator-only
        if not isinstance(message.channel, discord.DMChannel):
            return  # DMs only — no server channel posting

        text = message.content or ""
        images = await self._extract_images(message)

        # Voice memos: if an audio attachment is present, transcribe it and use
        # the transcript as the user message (Discord voice notes are typically
        # standalone — no caption text). If both audio + text are present, the
        # transcript is appended after the text for context.
        transcript = await self._transcribe_audio(message)
        if transcript:
            text = f"{text}\n\n[voice memo transcript]\n{transcript}".strip()

        # If nothing usable, ignore.
        if not text and not images:
            return

        await self._stream_to_discord(message, text, images)

    async def _extract_images(self, message: discord.Message) -> list[str]:
        """Read image attachments and convert to OpenAI multimodal data URLs."""
        images: list[str] = []
        for att in message.attachments:
            mime = (att.content_type or "").lower()
            if not mime.startswith("image/"):
                continue
            try:
                raw = await att.read()
                b64 = base64.b64encode(raw).decode("ascii")
                images.append(f"data:{mime};base64,{b64}")
            except Exception as e:  # noqa: BLE001
                log.warning("discord.image_attach_failed", error=str(e))
        return images

    async def _transcribe_audio(self, message: discord.Message) -> str | None:
        """Find first audio attachment, POST to /transcribe, return text.

        Returns None if no audio attachment, or empty string if transcription
        failed. Letting the caller decide whether to short-circuit the turn.
        """
        audio_att = None
        for att in message.attachments:
            mime = (att.content_type or "").lower()
            # Discord voice memos arrive as audio/ogg; user-uploaded files can
            # be audio/mpeg, audio/wav, audio/x-m4a, etc.
            if mime.startswith("audio/"):
                audio_att = att
                break
        if audio_att is None:
            return None

        try:
            audio_bytes = await audio_att.read()
        except Exception as e:  # noqa: BLE001
            log.warning("discord.audio_read_failed", error=str(e))
            return ""

        # POST as multipart/form-data to /api/transcribe (Whisper local).
        form = aiohttp.FormData()
        form.add_field(
            "audio",
            audio_bytes,
            filename=audio_att.filename or "voice.ogg",
            content_type=audio_att.content_type or "audio/ogg",
        )
        try:
            async with aiohttp.ClientSession() as http:
                async with http.post(
                    f"{self.lumos_url}/transcribe",
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.warning(
                            "discord.transcribe_failed",
                            status=resp.status,
                            body=body[:300],
                        )
                        return ""
                    data = await resp.json()
                    transcript = (data.get("text") or "").strip()
                    log.info(
                        "discord.transcribed",
                        len=len(transcript),
                        duration=data.get("duration"),
                    )
                    return transcript
        except Exception as e:  # noqa: BLE001
            log.warning("discord.transcribe_exception", error=str(e))
            return ""

    async def _stream_to_discord(
        self,
        trigger: discord.Message,
        user_text: str,
        images: list[str],
    ) -> None:
        """Post a placeholder, stream Lumos deltas into it via progressive edits."""
        channel = trigger.channel
        try:
            placeholder = await channel.send("…")
        except discord.HTTPException as e:
            log.warning("discord.placeholder_send_failed", error=str(e))
            return

        payload: dict[str, Any] = {
            "message": user_text or "[image only]",
            "images": images,
        }
        if self.session_id is None:
            # First message of this process — request history restore so Lumos
            # has prior turns in context.
            payload["restore_history"] = 10
        else:
            payload["session_id"] = self.session_id

        full_text = ""
        current_event = "message"
        last_edit = 0.0
        last_edit_content = "…"
        typing_task = asyncio.create_task(self._keep_typing(channel))

        try:
            async with aiohttp.ClientSession() as http:
                async with http.post(
                    f"{self.lumos_url}/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=600),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        await placeholder.edit(
                            content=f"[lumos /chat returned {resp.status}]\n```\n{body[:1500]}\n```"
                        )
                        return

                    buffer = ""
                    async for chunk in resp.content.iter_chunked(1024):
                        # Normalize CRLF / CR to LF — sse-starlette emits \r\n\r\n
                        # event separators by default, which would never match a
                        # \n\n split. Normalize at ingestion to handle both.
                        text = (
                            chunk.decode("utf-8", errors="replace")
                            .replace("\r\n", "\n")
                            .replace("\r", "\n")
                        )
                        buffer += text
                        # SSE events end with double newline.
                        while "\n\n" in buffer:
                            block, buffer = buffer.split("\n\n", 1)
                            event_name, data_str = _parse_sse_block(block)
                            if event_name:
                                current_event = event_name
                            if data_str is None:
                                continue
                            try:
                                data = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            if current_event == "session":
                                sid = data.get("session_id")
                                if sid:
                                    self.session_id = sid
                            elif current_event == "delta":
                                delta = data.get("text", "")
                                if delta:
                                    full_text += delta
                                    now = time.monotonic()
                                    if now - last_edit >= EDIT_INTERVAL_SEC:
                                        display = full_text[:DISCORD_MSG_MAX - 2] + " ▌"
                                        if display != last_edit_content:
                                            try:
                                                await placeholder.edit(content=display)
                                                last_edit_content = display
                                                last_edit = now
                                            except discord.HTTPException:
                                                pass
                            elif current_event == "error":
                                err = data.get("message", "unknown error")
                                await placeholder.edit(
                                    content=f"[lumos error]\n```\n{err[:1800]}\n```"
                                )
                                return
                            elif current_event == "done":
                                pass  # final state is handled below
        finally:
            typing_task.cancel()

        await self._finalize_message(channel, placeholder, full_text)

    async def _keep_typing(self, channel: discord.abc.Messageable) -> None:
        """Loop a typing indicator until cancelled."""
        try:
            while True:
                try:
                    await channel.typing()
                except discord.HTTPException:
                    pass
                await asyncio.sleep(TYPING_REFRESH_SEC)
        except asyncio.CancelledError:
            return

    async def _finalize_message(
        self,
        channel: discord.abc.Messageable,
        placeholder: discord.Message,
        full_text: str,
    ) -> None:
        """Final edit + spillover for long responses (Discord 2000-char limit)."""
        if not full_text:
            try:
                await placeholder.edit(content="[empty response]")
            except discord.HTTPException:
                pass
            return

        first = full_text[:DISCORD_MSG_MAX]
        try:
            await placeholder.edit(content=first)
        except discord.HTTPException:
            pass

        remaining = full_text[DISCORD_MSG_MAX:]
        while remaining:
            chunk = remaining[:DISCORD_MSG_MAX]
            remaining = remaining[DISCORD_MSG_MAX:]
            try:
                await channel.send(chunk)
            except discord.HTTPException as e:
                log.warning("discord.spillover_send_failed", error=str(e))
                return

    # ---- Slash commands -----------------------------------------------------

    def _register_commands(self) -> None:
        @self.tree.command(
            name="reset",
            description="Clear current Lumos session; next message starts fresh.",
        )
        async def reset_cmd(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.operator_id:
                await interaction.response.send_message(
                    "Not authorized.", ephemeral=True
                )
                return
            old = self.session_id
            self.session_id = None
            await interaction.response.send_message(
                f"Session reset. Previous: `{old or 'none'}`. "
                "Next DM will start a fresh session with history restored.",
                ephemeral=True,
            )

        @self.tree.command(
            name="status",
            description="Lumos URE-VM telemetry snapshot.",
        )
        async def status_cmd(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.operator_id:
                await interaction.response.send_message(
                    "Not authorized.", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                async with aiohttp.ClientSession() as http:
                    async with http.get(f"{self.lumos_url}/urevm") as resp:
                        data = await resp.json()
                tick = data.get("tick", 0)
                cycle = data.get("cycle_position", 0)
                impedance = data.get("impedance_accumulator", 0.0)
                resets = data.get("forbidden_resets", 0)
                msg = (
                    f"**URE-VM**\n"
                    f"tick: `{tick}`\n"
                    f"cycle: `{cycle}/370`\n"
                    f"Δ10i accum: `{impedance:.3f}`\n"
                    f"361 resets: `{resets}`\n"
                    f"session: `{self.session_id or 'fresh'}`"
                )
                await interaction.followup.send(msg, ephemeral=True)
            except Exception as e:  # noqa: BLE001
                await interaction.followup.send(
                    f"Status fetch failed: `{e}`", ephemeral=True
                )

        @self.tree.command(
            name="dream",
            description="Trigger Lumos's dream-consolidation cycle.",
        )
        async def dream_cmd(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.operator_id:
                await interaction.response.send_message(
                    "Not authorized.", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                async with aiohttp.ClientSession() as http:
                    async with http.post(
                        f"{self.lumos_url}/dream/run",
                        json={},
                        timeout=aiohttp.ClientTimeout(total=300),
                    ) as resp:
                        data = await resp.json()
                consolidated = data.get("consolidated", 0)
                skipped = data.get("skipped", False)
                msg = (
                    f"**Dream cycle complete**\n"
                    f"consolidated: `{consolidated}`\n"
                    f"skipped: `{skipped}`"
                )
                await interaction.followup.send(msg, ephemeral=True)
            except Exception as e:  # noqa: BLE001
                await interaction.followup.send(
                    f"Dream trigger failed: `{e}`", ephemeral=True
                )


def _parse_sse_block(block: str) -> tuple[str | None, str | None]:
    """Parse a single SSE event block. Returns (event_name, data_string)."""
    event_name: str | None = None
    data_lines: list[str] = []
    for line in block.splitlines():
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    data = "\n".join(data_lines) if data_lines else None
    return event_name, data


def run() -> None:
    """Entry point — read settings, construct client, run."""
    settings = get_settings()
    token = settings.discord_token.strip()
    operator_id_raw = settings.discord_operator_id.strip()

    if not token:
        raise RuntimeError(
            "LUMOS_DISCORD_TOKEN not set in .env. "
            "Get from discord.com/developers/applications → Bot → Reset Token."
        )
    if not operator_id_raw:
        raise RuntimeError(
            "LUMOS_DISCORD_OPERATOR_ID not set in .env. "
            "Enable Developer Mode in Discord → right-click your name → Copy User ID."
        )
    try:
        operator_id = int(operator_id_raw)
    except ValueError as e:
        raise RuntimeError(
            f"LUMOS_DISCORD_OPERATOR_ID must be a numeric Discord user ID, got: {operator_id_raw!r}"
        ) from e

    # Router is mounted at /api in api/app.py; bridge calls hit /api/chat,
    # /api/urevm, /api/dream/run, etc. so we bake the prefix into the base URL.
    lumos_url = f"http://{settings.host}:{settings.port}/api"

    log.info(
        "discord.starting",
        operator_id=operator_id,
        lumos_url=lumos_url,
    )

    bot = LumosDiscordBot(operator_id=operator_id, lumos_url=lumos_url)
    bot.run(token, log_handler=None)
