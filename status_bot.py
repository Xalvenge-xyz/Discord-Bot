import discord
from discord import ui, Embed, Color
from discord.ext import tasks
import requests
import json
import os
import asyncio
from bs4 import BeautifulSoup

CONFIG_FILE = "status_config.json"
STATUS_URL = "https://status.manifestor.cc/"
CHECK_INTERVAL = 5 * 60  # 5 minutes
BANNER_IMAGE_URL = "https://media.giphy.com/media/kyLYXonQYYfwYDIeZl/giphy.gif"


class StatusMonitor:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.config = self.load_config()
        self.status_loop.start()

    # -------- CONFIG --------
    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        return {}

    def save_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=4)

    # -------- SCRAPER --------
    @staticmethod
    def fetch_status():
        try:
            res = requests.get(STATUS_URL, timeout=10)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")

            blocks = soup.find_all(
                "div", class_="truncate text-xs font-semibold text-api-up"
            )

            if not blocks:
                return "ℹ️ Could not find status blocks"

            lines = []
            for idx, block in enumerate(blocks, start=1):
                text = block.text.strip()
                low = text.lower()

                if "ok" in low:
                    emoji = "✅"
                elif "maintenance" in low:
                    emoji = "⚠️"
                elif "down" in low:
                    emoji = "❌"
                else:
                    emoji = "ℹ️"

                lines.append(f"{emoji} Server {idx}: {text}")

            return "\n".join(lines)

        except Exception as e:
            return f"❌ Error fetching status: {e}"

    # -------- EMBED BUILDER --------
    async def send_visual_status(self, channel_id):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            print(f"[ERROR] Channel {channel_id} not found.")
            return

        try:
            status_text = self.fetch_status()
            remaining = CHECK_INTERVAL

            embed = Embed(
                title="🔔 Manifestor.cc Real-Time Status",
                description=status_text,
                color=Color.blurple()
            )
            embed.set_image(url=BANNER_IMAGE_URL)
            embed.set_footer(text=f"Next update in {remaining // 60:02d}:{remaining % 60:02d}")

            msg = await channel.send(embed=embed)

        except discord.errors.Forbidden:
            print(f"[ERROR] Missing permission in channel {channel_id}")
            return

        # Live countdown
        while remaining > 0:
            mins, secs = divmod(remaining, 60)
            embed.set_footer(text=f"Next update in {mins:02d}:{secs:02d}")

            try:
                await msg.edit(embed=embed)
            except discord.errors.Forbidden:
                return

            await asyncio.sleep(1)
            remaining -= 1

        embed.description = self.fetch_status()
        embed.set_footer(text="Next update in 05:00")

        try:
            await msg.edit(embed=embed)
        except discord.errors.Forbidden:
            pass

    # -------- LOOP --------
    @tasks.loop(seconds=CHECK_INTERVAL)
    async def status_loop(self):
        await self.bot.wait_until_ready()
        for guild_id, channel_id in self.config.items():
            await self.send_visual_status(channel_id)


# =============  SLASH COMMAND (OUTSIDE CLASS!)  =============
def create_setting_command(monitor):
    async def setting(interaction: discord.Interaction):

        # STRICT OWNER CHECK BEFORE ANY UI IS CREATED
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message(
                "❌ Only the **server owner** can use this command.",
                ephemeral=True
            )
            return  # ⬅ IMPORTANT: STOP FUNCTION HERE

        # ---- OWNER ONLY FROM THIS POINT ----

        channels = interaction.guild.text_channels[:25]
        options = [
            discord.SelectOption(label=c.name, value=str(c.id))
            for c in channels
        ]

        class ChannelSelect(ui.Select):
            def __init__(self):
                super().__init__(
                    placeholder="Select a channel for status updates",
                    min_values=1,
                    max_values=1,
                    options=options
                )

            async def callback(self, select_interaction: discord.Interaction):
                selected = int(self.values[0])
                await select_interaction.response.defer()

                monitor.config[str(interaction.guild.id)] = selected
                monitor.save_config()

                await select_interaction.followup.send(
                    f"✅ Status channel set to <#{selected}>",
                    ephemeral=True
                )

        view = ui.View()
        view.add_item(ChannelSelect())

        await interaction.response.send_message(
            "📌 Select the channel for Manifestor status:",
            view=view,
            ephemeral=True
        )

    return discord.app_commands.Command(
        name="setting",
        description="Configure the status channel (Owner Only)",
        callback=setting
    )

