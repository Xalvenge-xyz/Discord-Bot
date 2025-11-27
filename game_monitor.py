# game_monitor.py
import discord
from discord import ui, app_commands, Embed, Color
from discord.ext import tasks
import aiohttp
import asyncio
import json
import os
import re
from typing import List, Dict, Any, Optional

CONFIG_FILE = "game_config.json"
GAMES_JSON_URL = "https://generator.ryuu.lol/files/games.json"
FIXES_PAGE_URL = "https://generator.ryuu.lol/fixes"

# Tunables
REQUEST_TIMEOUT = 8
TCP_LIMIT = 100
LOOP_INTERVAL_MINUTES = 5

class GameMonitor:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.config = self.load_config()

        # Persisted sets for each feature
        self.seen_new = set(self.config.get("seen_new", []))
        self.seen_update = set(self.config.get("seen_update", []))
        self.seen_fixed = set(self.config.get("seen_fixed", []))

        # ensure channel keys exist
        self.config.setdefault("channel_id_new", None)
        self.config.setdefault("channel_id_update", None)
        self.config.setdefault("channel_id_fixed", None)

        # aiohttp settings
        self.session_timeout = aiohttp.ClientTimeout(total=None)
        self.connector = aiohttp.TCPConnector(limit=TCP_LIMIT)

        # start background loop
        self.monitor_loop.start()

    # ---------- config ----------
    def load_config(self) -> Dict[str, Any]:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def save_config(self):
        self.config["seen_new"] = list(self.seen_new)
        self.config["seen_update"] = list(self.seen_update)
        self.config["seen_fixed"] = list(self.seen_fixed)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)

    # ---------- safe fetch ----------
    async def safe_get_json(self, session: aiohttp.ClientSession, url: str) -> Optional[Any]:
        try:
            async with session.get(url, timeout=REQUEST_TIMEOUT) as r:
                if r.status != 200:
                    return None
                try:
                    return await r.json()
                except Exception:
                    return None
        except asyncio.TimeoutError:
            return None
        except Exception:
            return None

    async def safe_get_text(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        try:
            async with session.get(url, timeout=REQUEST_TIMEOUT) as r:
                if r.status != 200:
                    return None
                try:
                    return await r.text()
                except Exception:
                    return None
        except asyncio.TimeoutError:
            return None
        except Exception:
            return None

    # ---------- games (fast JSON) ----------
    async def fetch_games(self):
        """Load all games from the fast JSON endpoint."""
        url = "https://generator.ryuu.lol/files/games.json"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as r:
                    if r.status != 200:
                        print("[ERROR] Failed to fetch games.json:", r.status)
                        return []

                    data = await r.json()

                    if isinstance(data, list):
                        return data  # correct format: list of games

                    print("[ERROR] Invalid JSON format from games.json")
                    return []

        except Exception as e:
            print("[ERROR] Exception in fetch_games():", e)
            return []


    # ---------- fixes (HTML parse fallback) ----------
    async def fetch_fixes(self) -> List[Dict[str, Any]]:
        """
        Fetches the /fixes HTML page and extracts .file-item anchor blocks.
        Each block yields: title (filename without .zip), download link, size (if present).
        """
        async with aiohttp.ClientSession(timeout=self.session_timeout, connector=self.connector) as session:
            html = await self.safe_get_text(session, FIXES_PAGE_URL)
            if not html:
                return []

            results = []
            # find anchors with class "file-item"
            # pattern: <a ... class="file-item" href="..."> ... <div class="file-name">NAME.zip</div> ... </a>
            anchors = re.findall(r'(<a[^>]*class=["\'][^"\']*file-item[^"\']*["\'][\s\S]*?>[\s\S]*?</a>)', html, flags=re.I)
            for a_html in anchors:
                # href
                href_m = re.search(r'href=["\']([^"\']+)["\']', a_html)
                href = href_m.group(1) if href_m else None
                # file-name div
                name_m = re.search(r'<div[^>]*class=["\']file-name["\'][^>]*>(.*?)</div>', a_html, flags=re.I|re.S)
                raw_name = name_m.group(1).strip() if name_m else None
                # file-size optional
                size_m = re.search(r'<div[^>]*class=["\']file-size["\'][^>]*>(.*?)</div>', a_html, flags=re.I|re.S)
                size = size_m.group(1).strip() if size_m else None

                if raw_name:
                    # strip .zip or .rar etc
                    title = re.sub(r'\.(zip|rar|7z|tar\.gz)$', '', raw_name, flags=re.I).strip()
                else:
                    # fallback title from href
                    if href:
                        title = href.rstrip('/').split('/')[-1]
                        title = re.sub(r'%20', ' ', title)
                        title = re.sub(r'\.(zip|rar|7z|tar\.gz)$', '', title, flags=re.I)
                    else:
                        continue

                # make absolute URL if needed
                if href and href.startswith('/'):
                    href = "https://generator.ryuu.lol" + href
                results.append({"title": title, "download": href or "", "size": size or ""})

            # dedupe by title preserving order
            seen = set()
            uniq = []
            for item in results:
                if item["title"] in seen:
                    continue
                seen.add(item["title"])
                uniq.append(item)
            return uniq

    # ---------- embed helpers ----------
    def make_game_embed(self, name: str, appid: str, image: Optional[str], kind: str) -> Embed:
        """
        Professional embed for New/Updated games (title, appid, large image banner).
        kind = "NEW" or "UPDATED"
        """
        embed = Embed(
            title=f"🎮 {name}",
            description=f"📦 **Manifest for App ID:** `{appid}`\n• **Type:** {kind}",
            color=Color.blurple() if kind in ("NEW", "UPDATED") else Color.green()
        )
        if image:
            embed.set_image(url=image)
        embed.set_footer(text="Steam Manifest Bot • Powered by JAY CAPARIDA AKA XALVENGE D.")
        return embed

    def make_fix_embed(self, name: str, download_url: str, size: str) -> Embed:
        """
        Simpler embed for fixes: title + download link (per your instruction).
        """
        embed = Embed(
            title=f"🛠️ {name}",
            description=f"📥 [Download ZIP]({download_url})\n{('• Size: ' + size) if size else ''}",
            color=Color.green()
        )
        embed.set_footer(text="Fix posted by Steam Manifest Bot • XALVENGE D.")
        return embed

    async def safe_send(self, channel_id: int, embed: Embed):
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                channel = None
        if not channel:
            print(f"[ERROR] Channel {channel_id} not found for posting.")
            return
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            print(f"[ERROR] Missing access to channel {channel_id}")
        except Exception as e:
            print(f"[ERROR] Failed to send embed to {channel_id}: {e}")

    # ---------- processing ----------
    async def process_games_new_updated(self):
        games = await self.fetch_games()
        if not games:
            return

        current_keys = set()
        mapping = {}
        # key by name (title) primarily
        for g in games:
            name = (g.get("title") or g.get("name") or "").strip()
            appid = str(g.get("appid") or g.get("id") or "N/A")
            image = g.get("img") or g.get("image") or g.get("header_image") or None
            if not name:
                name = f"Unknown Game ({appid})"
            key = name
            current_keys.add(key)
            mapping[key] = {"name": name, "appid": appid, "img": image}

        new_keys = current_keys - self.seen_new
        update_keys = current_keys - self.seen_update

        ch_new = self.config.get("channel_id_new")
        ch_update = self.config.get("channel_id_update")

        # Post NEW (automatic)
        if ch_new and new_keys:
            for k in sorted(new_keys):
                e = mapping.get(k)
                if not e:
                    continue
                embed = self.make_game_embed(e["name"], e["appid"], e["img"], "NEW")
                await self.safe_send(ch_new, embed)

        # Post UPDATED (automatic) - avoid reposting ones just sent as NEW
        if ch_update and update_keys:
            for k in sorted(update_keys):
                if k in new_keys:
                    continue
                e = mapping.get(k)
                if not e:
                    continue
                embed = self.make_game_embed(e["name"], e["appid"], e["img"], "UPDATED")
                await self.safe_send(ch_update, embed)

        # update seen sets and save
        if new_keys:
            self.seen_new.update(new_keys)
        if update_keys:
            self.seen_update.update(update_keys)
        if new_keys or update_keys:
            self.save_config()

    async def process_fixes(self):
        fixes = await self.fetch_fixes()
        if not fixes:
            return

        current_titles = set()
        mapping = {}
        for f in fixes:
            name = (f.get("title") or f.get("name") or "").strip()
            download = f.get("download") or f.get("url") or ""
            size = f.get("size") or ""
            if not name:
                continue
            key = name
            current_titles.add(key)
            mapping[key] = {"title": name, "download": download, "size": size}

        new_fixed = current_titles - self.seen_fixed
        ch_fixed = self.config.get("channel_id_fixed")

        if ch_fixed and new_fixed:
            for k in sorted(new_fixed):
                e = mapping.get(k)
                if not e:
                    continue
                embed = self.make_fix_embed(e["title"], e["download"], e["size"])
                await self.safe_send(ch_fixed, embed)

        if new_fixed:
            self.seen_fixed.update(new_fixed)
            self.save_config()

    async def check_for_new_games(self):
        # first new/updated, then fixes
        await self.process_games_new_updated()
        await self.process_fixes()

    # ---------- tasks loop ----------
    @tasks.loop(minutes=LOOP_INTERVAL_MINUTES)
    async def monitor_loop(self):
        await self.bot.wait_until_ready()
        try:
            await self.check_for_new_games()
        except Exception as e:
            print(f"[ERROR] Monitor loop exception: {e}")

    @monitor_loop.before_loop
    async def before_monitor(self):
        await self.bot.wait_until_ready()


# ---------- Slash command creators (to be registered in manifest.py on_ready) ----------
def create_gamesetup_command(monitor: GameMonitor):
    async def gamesetup(interaction: discord.Interaction):
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("❌ Only the server owner can use this command.", ephemeral=True)
            return

        channels = interaction.guild.text_channels[:25]
        options = [discord.SelectOption(label=c.name, value=str(c.id)) for c in channels]

        feature_options = [
            discord.SelectOption(label="New Games", value="new"),
            discord.SelectOption(label="Updated Games", value="update"),
            discord.SelectOption(label="Fixed Games", value="fixed")
        ]

        class FeatureSelect(ui.Select):
            def __init__(self):
                super().__init__(placeholder="Select feature to configure", min_values=1, max_values=1, options=feature_options)

            async def callback(self, feature_interaction: discord.Interaction):
                feature = self.values[0]

                class ChannelSelect(ui.Select):
                    def __init__(self):
                        super().__init__(placeholder=f"Select channel for {feature} alerts", min_values=1, max_values=1, options=options)

                    async def callback(self, select_interaction: discord.Interaction):
                        selected_channel = int(self.values[0])
                        if feature == "new":
                            monitor.config["channel_id_new"] = selected_channel
                        elif feature == "update":
                            monitor.config["channel_id_update"] = selected_channel
                        elif feature == "fixed":
                            monitor.config["channel_id_fixed"] = selected_channel
                        monitor.save_config()
                        await select_interaction.response.send_message(f"✅ Channel for **{feature} games** set to <#{selected_channel}>", ephemeral=True)

                view2 = ui.View()
                view2.add_item(ChannelSelect())
                await feature_interaction.response.send_message(f"📌 Now select the channel for **{feature} alerts**:", view=view2, ephemeral=True)

        view = ui.View()
        view.add_item(FeatureSelect())
        await interaction.response.send_message("📌 Select which feature you want to configure:", view=view, ephemeral=True)

    return app_commands.Command(name="gamesetup", description="Configure channels for new/updated/fixed game alerts (Owner Only)", callback=gamesetup)


def create_testgamealerts_command(monitor: GameMonitor):
    async def testgamealerts(interaction: discord.Interaction):
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("❌ Only the server owner can use this command.", ephemeral=True)
            return

        sent = []
        for feature, key in (("New", "channel_id_new"), ("Updated", "channel_id_update"), ("Fixed", "channel_id_fixed")):
            channel_id = monitor.config.get(key)
            if not channel_id:
                continue
            test_embed = Embed(
                title=f"🎮 TEST {feature.upper()} GAME ALERT",
                description="📦 **Manifest for App ID:** `123456`",
                color=Color.green() if feature == "Fixed" else Color.blurple()
            )
            test_embed.set_image(url="https://i.imgur.com/OBaYQvr.jpeg")
            test_embed.set_footer(text="Steam Manifest Bot • XALVENGE D.")
            await monitor.safe_send(channel_id, test_embed)
            sent.append(feature)

        if not sent:
            await interaction.response.send_message("⚠ No channels configured. Run `/gamesetup` first.", ephemeral=True)
            return

        await interaction.response.send_message(f"✅ Test alerts sent for: {', '.join(sent)}", ephemeral=True)

    return app_commands.Command(name="testgamealerts", description="Send a test game alert embed (Owner Only)", callback=testgamealerts)


def create_gamelist_command(monitor: GameMonitor):
    async def gamelist(interaction: discord.Interaction):
        await interaction.response.defer()
        games = await monitor.fetch_games()
        if not games:
            await interaction.followup.send("❌ Failed to load game list.", ephemeral=True)
            return

        # format games
        formatted = []
        for g in games:
            title = g.get("title") or g.get("name") or "Unknown Game"
            appid = g.get("appid") or g.get("id") or "N/A"
            formatted.append(f"● **{title}** — `{appid}`")

        # chunk into groups of 80 lines
        chunks = [formatted[i:i+80] for i in range(0, len(formatted), 80)]

        embeds = []
        for idx, chunk in enumerate(chunks, start=1):
            text = "\n".join(chunk)
            embed = Embed(
                title=f"📃 Game List ({len(games)} total) — Page {idx}/{len(chunks)}",
                description=text[:4096],  # discord safety
                color=Color.blurple()
            )
            embed.set_footer(text="Steam Manifest Bot • XALVENGE D.")
            embeds.append(embed)

        # send in order + 2 second delay per embed
        msg = await interaction.followup.send(embed=embeds[0])
        msg = await msg.fetch()

        # Edit the same message for subsequent pages
        for embed in embeds[1:]:
            await asyncio.sleep(2)  # your delay
            try:
                await msg.edit(embed=embed)
            except Exception as e:
                print("[ERROR] Failed to edit message:", e)

    return app_commands.Command(
        name="gamelist",
        description="List all games (80 per embed, multi-page)",
        callback=gamelist
    )



# ---------- New commands you asked for ----------
def create_newgame_command(monitor: GameMonitor):
    async def newgame(interaction: discord.Interaction):
        """
        Manual command: fetch what would be considered 'new' since last saved seen_new set,
        but DOES NOT modify the seen set (so automatic alerts remain unchanged).
        """
        await interaction.response.defer()
        games = await monitor.fetch_games()
        if not games:
            await interaction.followup.send("❌ Failed to load game list.", ephemeral=True)
            return

        current_keys = []
        mapping = {}
        for g in games:
            name = (g.get("title") or g.get("name") or "").strip()
            appid = str(g.get("appid") or g.get("id") or "N/A")
            image = g.get("img") or g.get("image") or g.get("header_image") or None
            if not name:
                name = f"Unknown Game ({appid})"
            key = name
            current_keys.append(key)
            mapping[key] = {"name": name, "appid": appid, "img": image}

        new_keys = [k for k in current_keys if k not in monitor.seen_new]
        if not new_keys:
            await interaction.followup.send("⚠ No newly added games found.", ephemeral=True)
            return

        # send the first 5 in a neat embed collection (or paginate later)
        for k in new_keys[:10]:
            e = mapping.get(k)
            embed = monitor.make_game_embed(e["name"], e["appid"], e["img"], "NEW")
            await interaction.followup.send(embed=embed)
        # if there are more, tell user how many
        if len(new_keys) > 10:
            await interaction.followup.send(f"✅ {len(new_keys)} new games found — showing first 10.", ephemeral=True)

    return app_commands.Command(name="newgame", description="Show newly added games (does not modify automatic seen sets)", callback=newgame)


def create_fixegame_command(monitor: GameMonitor):
    async def fixegame(interaction: discord.Interaction):
        """
        Manual command: fetch all fixes and show them (does not modify seen_fixed).
        """
        await interaction.response.defer()
        fixes = await monitor.fetch_fixes()
        if not fixes:
            await interaction.followup.send("❌ Failed to load fixes.", ephemeral=True)
            return

        # send ALL fixes
        for f in fixes:
            embed = monitor.make_fix_embed(f.get("title"), f.get("download"), f.get("size", ""))
            await interaction.followup.send(embed=embed)

        await interaction.followup.send(f"✅ {len(fixes)} fixes found — all displayed.", ephemeral=True)

    return app_commands.Command(name="fixegame", description="Show current fixed games (does not modify automatic seen sets)", callback=fixegame)

