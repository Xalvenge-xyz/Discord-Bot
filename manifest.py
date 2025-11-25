import discord
from discord import app_commands
from discord.ext import commands
from status_bot import StatusMonitor
import requests
from io import BytesIO


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# @bot.event
# async def on_ready():
#     guild = discord.Object(id=GUILD_ID)
#     await bot.tree.sync(guild=guild)
#     print(f"{bot.user} is online and commands are synced!")

from status_bot import StatusMonitor, create_setting_command

@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)

    # instantiate the monitor
    monitor = StatusMonitor(bot)

    # add the /setting command for the guild only (instant visibility)
    bot.tree.add_command(create_setting_command(monitor), guild=guild)

    # sync commands for this guild
    await bot.tree.sync(guild=guild)

    print(f"{bot.user} is online and commands are synced!")


def get_steam_info(appid):
    """Fetch Steam game name + image URL."""
    try:
        url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
        data = requests.get(url).json()

        if not data[str(appid)]["success"]:
            return None

        info = data[str(appid)]["data"]
        return {
            "name": info.get("name", "Unknown Game"),
            "image": info.get("header_image", None)
        }

    except:
        return None


@bot.tree.command(name="manifest", description="Get a Steam manifest file with game info")
@app_commands.describe(appid="Enter the Steam App ID")
async def manifest(interaction: discord.Interaction, appid: str):

    if not appid.isdigit():
        await interaction.response.send_message("❌ App ID must be numbers only!", ephemeral=True)
        return

    await interaction.response.defer()

    # Get Steam info (game name + image)
    info = get_steam_info(appid)
    if not info:
        await interaction.followup.send("❌ Game not found on Steam.")
        return

    game_name = info["name"]
    game_image = info["image"]

    # Download manifest
    manifest_url = f"https://manifestor.cc/?appid={appid}&source=github"
    res = requests.get(manifest_url)

    if res.status_code != 200 or len(res.content) < 50:
        await interaction.followup.send("❌ Manifest not found. Try another App ID.")
        return

    file_bytes = BytesIO(res.content)
    file_bytes.seek(0)

    # Create professional embed
    embed = discord.Embed(
        title=f"🎮 {game_name}",
        description=f"📦 **Manifest for App ID:** `{appid}`",
        color=discord.Color.blurple()  # clean, professional color
    )

    if game_image:
        embed.set_image(url=game_image)  # big banner style

    embed.set_footer(text="Steam Manifest Bot • Powered by JAY CAPARIDA AKA XALVENGE D.")

    # Send reply with embed + file
    await interaction.followup.send(
        embed=embed,
        file=discord.File(file_bytes, filename=f"{appid}.rar")
    )


bot.run(TOKEN)