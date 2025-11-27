import discord
from discord import app_commands
from discord.ext import commands
from status_bot import StatusMonitor
import requests
from io import BytesIO
from status_bot import StatusMonitor, create_setting_command
from game_monitor import GameMonitor, create_gamesetup_command
from game_monitor import (
    GameMonitor,
    create_gamesetup_command,
    create_gamelist_command,
    create_testgamealerts_command,
    create_newgame_command,
    create_fixegame_command,
    # create_resetgames_command
)


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)

    # Instantiate the monitors
    monitor = StatusMonitor(bot)
    game_monitor = GameMonitor(bot)

    # Status monitor commands
    bot.tree.add_command(create_setting_command(monitor), guild=guild)

    # Game monitor commands (owner only)
    bot.tree.add_command(create_gamesetup_command(game_monitor), guild=guild)
    bot.tree.add_command(create_testgamealerts_command(game_monitor), guild=guild)
    # bot.tree.add_command(create_resetgames_command(game_monitor), guild=guild)
    bot.tree.add_command(create_newgame_command(game_monitor), guild=guild)
    bot.tree.add_command(create_fixegame_command(game_monitor), guild=guild)


    # Public command for everyone
    bot.tree.add_command(create_gamelist_command(game_monitor), guild=guild)

    # Sync commands
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