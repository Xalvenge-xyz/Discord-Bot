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

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        return {}

    def save_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=4)

    # ---------------- STATUS CHECK ----------------
    @staticmethod
    def fetch_status():
        try:
            res = requests.get(STATUS_URL, timeout=10)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")

            # Find all status divs
            status_blocks = soup.find_all("div", class_="truncate text-xs font-semibold text-api-up")

            if not status_blocks:
                return "ℹ️ Could not find status blocks"

            status_lines = []
            for idx, block in enumerate(status_blocks, start=1):
                status_text = block.text.strip()
                # Map emoji
                st_lower = status_text.lower()
                if "ok" in st_lower:
                    emoji = "✅"
                elif "maintenance" in st_lower:
                    emoji = "⚠️"
                elif "down" in st_lower:
                    emoji = "❌"
                else:
                    emoji = "ℹ️"

                # Since component names are not in that div, just number them
                status_lines.append(f"{emoji} Server {idx}: {status_text}")

            return "\n".join(status_lines)

        except Exception as e:
            return f"❌ Error fetching status: {e}"


    # ---------------- VISUAL STATUS ----------------
    async def send_visual_status(self, channel_id):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            print(f"Channel {channel_id} not found.")
            return

        # Initial status fetch
        status_msg = self.fetch_status()

        remaining = CHECK_INTERVAL
        embed = Embed(
            title="🔔 Manifestor.cc Real-Time Status",
            description=status_msg,
            color=Color.blurple()
        )
        embed.set_image(url=BANNER_IMAGE_URL)
        embed.set_footer(text=f"Next update in {remaining//60:02d}:{remaining%60:02d}")

        msg = await channel.send(embed=embed)

        # Countdown loop updating footer
        while remaining > 0:
            minutes, seconds = divmod(remaining, 60)
            embed.set_footer(text=f"Next update in {minutes:02d}:{seconds:02d}")
            try:
                await msg.edit(embed=embed)
            except discord.errors.Forbidden:
                print(f"Missing permission to edit message in channel {channel_id}")
                return
            await asyncio.sleep(1)
            remaining -= 1

        # After countdown, fetch new status
        status_msg = self.fetch_status()
        embed.description = status_msg
        remaining = CHECK_INTERVAL
        embed.set_footer(text=f"Next update in {remaining//60:02d}:{remaining%60:02d}")
        await msg.edit(embed=embed)

    # ---------------- BACKGROUND LOOP ----------------
    @tasks.loop(seconds=CHECK_INTERVAL)
    async def status_loop(self):
        await self.bot.wait_until_ready()
        for guild_id, channel_id in self.config.items():
            await self.send_visual_status(channel_id)

# ----------------- SLASH COMMAND -----------------
def create_setting_command(monitor: StatusMonitor):
    bot = monitor.bot

    async def setting(interaction: discord.Interaction):

        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message(
                "❌ Only the server owner can use this command.", ephemeral=True
            )
            return
        
        channels = [c for c in interaction.guild.text_channels][:25]
        options = [discord.SelectOption(label=c.name, value=str(c.id)) for c in channels]

        class ChannelSelect(ui.Select):
            def __init__(self):
                super().__init__(
                    placeholder="Select a channel for status updates",
                    min_values=1,
                    max_values=1,
                    options=options
                )

            async def callback(self, select_interaction: discord.Interaction):
                selected_id = int(self.values[0])
                await select_interaction.response.defer()
                monitor.config[str(interaction.guild.id)] = selected_id
                monitor.save_config()
                await select_interaction.followup.send(
                    f"✅ Status channel set to <#{selected_id}>", ephemeral=True
                )

        view = ui.View()
        view.add_item(ChannelSelect())
        await interaction.response.send_message(
            "Select a channel for status updates:", view=view, ephemeral=True
        )

    return discord.app_commands.Command(
        name="setting",
        description="Configure status channel",
        callback=setting
    )
