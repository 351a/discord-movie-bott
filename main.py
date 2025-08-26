import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
import json
from typing import Dict, List
import aiohttp
import re
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot configuration
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix='/', intents=intents)

# Configuration - Use environment variables for Railway
BOT_TOKEN = os.getenv("DISCORD_TOKEN") or "YOUR_BOT_TOKEN_HERE"
MOVIE_LIST_FILE = "movies.json"  # JSON file containing movie list and Google Drive URLs

class MovieBot:
    def __init__(self):
        self.current_streams: Dict[int, any] = {}  # guild_id -> voice_client
        self.movie_list = self.load_movie_list()
    
    def load_movie_list(self) -> Dict[str, str]:
        """Load movie list from JSON file or create default"""
        try:
            with open(MOVIE_LIST_FILE, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            # Create default movie list with example Google Drive links
            default_movies = {
                # Add your Google Drive links here
                # Format: "moviename": "https://drive.google.com/uc?id=FILE_ID"
            }
            self.save_movie_list(default_movies)
            logger.info(f"Created {MOVIE_LIST_FILE}. Add movies using /add_movie command.")
            return default_movies
    
    def save_movie_list(self, movie_list: Dict[str, str]):
        """Save movie list to JSON file"""
        with open(MOVIE_LIST_FILE, 'w') as f:
            json.dump(movie_list, f, indent=2)
    
    def convert_drive_url(self, url: str) -> str:
        """Convert Google Drive share URL to direct download URL"""
        # Extract file ID from various Google Drive URL formats
        patterns = [
            r'https://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)',
            r'https://drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)',
            r'https://docs\.google\.com/.*?/d/([a-zA-Z0-9_-]+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                file_id = match.group(1)
                return f"https://drive.google.com/uc?export=download&id={file_id}"
        
        # If already a direct download URL or different format, return as-is
        return url
    
    def get_movie_url(self, movie_name: str) -> str:
        """Get movie URL by name (case insensitive)"""
        movie_name = movie_name.lower().replace(" ", "").replace("-", "").replace("_", "")
        for key, url in self.movie_list.items():
            if key.lower().replace(" ", "").replace("-", "").replace("_", "") == movie_name:
                return self.convert_drive_url(url)
        return None
    
    def find_movie_name(self, movie_name: str) -> str:
        """Find exact movie name from partial match"""
        movie_name = movie_name.lower().replace(" ", "").replace("-", "").replace("_", "")
        for key in self.movie_list.keys():
            if key.lower().replace(" ", "").replace("-", "").replace("_", "") == movie_name:
                return key
        return None
    
    def list_movies(self) -> List[str]:
        """Get list of available movies"""
        return list(self.movie_list.keys())
    
    async def verify_url(self, url: str) -> bool:
        """Verify if the Google Drive URL is accessible"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(url, allow_redirects=True, timeout=10) as response:
                    return response.status in [200, 206]  # 206 for partial content
        except:
            return False

movie_bot = MovieBot()

# Autocomplete function for movie names
async def movie_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    movies = movie_bot.list_movies()
    if not current:
        # Return first 25 movies if no input
        return [
            app_commands.Choice(name=movie, value=movie)
            for movie in movies[:25]
        ]
    
    # Filter movies that start with or contain the current input
    filtered = []
    current_lower = current.lower()
    
    # First, add movies that start with the input
    for movie in movies:
        if movie.lower().startswith(current_lower):
            filtered.append(movie)
    
    # Then, add movies that contain the input but don't start with it
    for movie in movies:
        if current_lower in movie.lower() and not movie.lower().startswith(current_lower):
            filtered.append(movie)
    
    # Return first 25 matches
    return [
        app_commands.Choice(name=movie, value=movie)
        for movie in filtered[:25]
    ]

@bot.event
async def on_ready():
    logger.info(f'{bot.user} has connected to Discord!')
    logger.info(f'Bot is in {len(bot.guilds)} guilds')
    
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")

@bot.tree.command(name="play", description="Get movie link and join voice channel for watch party")
@app_commands.autocomplete(movie=movie_autocomplete)
async def play_movie(interaction: discord.Interaction, movie: str):
    """Start a movie watch party - bot joins voice channel and provides movie link"""
    
    # Check if user is in a voice channel
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("❌ You need to be in a voice channel to start a watch party!", ephemeral=True)
        return
    
    # Get movie URL and exact name
    movie_url = movie_bot.get_movie_url(movie)
    exact_movie_name = movie_bot.find_movie_name(movie)
    
    if not movie_url or not exact_movie_name:
        available_movies = ", ".join(movie_bot.list_movies()[:10])  # Show first 10
        if not movie_bot.list_movies():
            available_movies = "No movies available! Use `/add_movie` to add some."
        elif len(movie_bot.list_movies()) > 10:
            available_movies += f"... and {len(movie_bot.list_movies()) - 10} more"
            
        await interaction.response.send_message(
            f"❌ Movie '{movie}' not found!\n**Available movies:** {available_movies}\n\n*Use the autocomplete feature by typing movie names!*", 
            ephemeral=True
        )
        return
    
    # Check if bot is already in a voice channel in this guild
    guild_id = interaction.guild_id
    if guild_id in movie_bot.current_streams:
        current_vc = movie_bot.current_streams[guild_id]
        if current_vc.channel:
            await interaction.response.send_message(f"❌ Already in voice channel **{current_vc.channel.name}**! Use `/stop` first.", ephemeral=True)
            return
    
    await interaction.response.defer()
    
    try:
        # Verify URL is accessible
        if not await movie_bot.verify_url(movie_url):
            embed = discord.Embed(
                title="⚠️ Movie URL Issue",
                description=f"**{exact_movie_name}** - The Google Drive link may not be publicly accessible.",
                color=0xff9900
            )
            embed.add_field(
                name="How to fix:",
                value="1. Right-click the file in Google Drive\n2. Select 'Share'\n3. Change to 'Anyone with the link'\n4. Set permission to 'Viewer'",
                inline=False
            )
            embed.add_field(name="Try anyway?", value=f"[Direct Link]({movie_url})", inline=False)
            await interaction.followup.send(embed=embed)
            return
        
        # Join voice channel
        voice_channel = interaction.user.voice.channel
        voice_client = await voice_channel.connect()
        
        # Store the voice client reference
        movie_bot.current_streams[guild_id] = voice_client
        
        # Create watch party embed
        embed = discord.Embed(
            title="🎬 Movie Watch Party Started!",
            description=f"**Movie:** {exact_movie_name}\n**Voice Channel:** {voice_channel.name}",
            color=0x00ff00
        )
        
        embed.add_field(
            name="🔗 Watch Here",
            value=f"[**Click to Open Movie**]({movie_url})\n*Everyone click this link to watch together!*",
            inline=False
        )
        
        embed.add_field(
            name="📋 Instructions",
            value="1. **Everyone** click the movie link above\n2. **Stay** in the voice channel to chat\n3. **Coordinate** play/pause in voice chat\n4. Use `/stop` when done",
            inline=False
        )
        
        embed.add_field(
            name="💡 Pro Tips",
            value="• Use Discord's screen share for better sync\n• Nominate someone as the 'play button coordinator'\n• Use voice chat for reactions and commentary!",
            inline=False
        )
        
        embed.set_footer(text="🤖 Bot will stay in voice channel until everyone leaves or /stop is used")
        
        await interaction.followup.send(embed=embed)
        
        # Send a follow-up message with just the clean link
        await interaction.followup.send(
            f"🎥 **Direct Movie Link:** {movie_url}\n\n*Copy this link if needed!*",
            ephemeral=False
        )
        
    except discord.errors.ClientException as e:
        if "already connected" in str(e).lower():
            await interaction.followup.send("❌ Bot is already connected to a voice channel in this server!")
        else:
            await interaction.followup.send(f"❌ Could not join voice channel: {str(e)}")
    except Exception as e:
        logger.error(f"Error starting watch party: {e}")
        if guild_id in movie_bot.current_streams:
            del movie_bot.current_streams[guild_id]
        try:
            if 'voice_client' in locals():
                await voice_client.disconnect()
        except:
            pass
        await interaction.followup.send(f"❌ Error starting watch party: {str(e)}")

@bot.tree.command(name="stop", description="End watch party and leave voice channel")
async def stop_movie(interaction: discord.Interaction):
    """End the watch party and leave voice channel"""
    guild_id = interaction.guild_id
    
    if guild_id not in movie_bot.current_streams:
        await interaction.response.send_message("❌ No watch party is currently active!", ephemeral=True)
        return
    
    try:
        voice_client = movie_bot.current_streams[guild_id]
        channel_name = voice_client.channel.name if voice_client.channel else "Unknown"
        
        await voice_client.disconnect()
        del movie_bot.current_streams[guild_id]
        
        embed = discord.Embed(
            title="🎬 Watch Party Ended",
            description=f"Left voice channel: **{channel_name}**\nThanks for watching together! 🍿",
            color=0xff6b6b
        )
        
        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
        logger.error(f"Error ending watch party: {e}")
        await interaction.response.send_message(f"❌ Error ending watch party: {str(e)}")

@bot.tree.command(name="movies", description="List available movies")
async def list_movies(interaction: discord.Interaction):
    """List all available movies"""
    movies = movie_bot.list_movies()
    
    if not movies:
        embed = discord.Embed(
            title="🎬 No Movies Available",
            description="No movies have been added yet!",
            color=0xff9900
        )
        embed.add_field(name="How to add movies:", value="Use `/add_movie <n> <google_drive_url>`", inline=False)
        embed.add_field(name="Example:", value="`/add_movie Superman https://drive.google.com/file/d/abc123...`", inline=False)
        await interaction.response.send_message(embed=embed)
        return
    
    # Split movies into chunks if too many
    movie_chunks = [movies[i:i+20] for i in range(0, len(movies), 20)]
    
    for i, chunk in enumerate(movie_chunks):
        embed = discord.Embed(
            title=f"🎬 Available Movies {f'({i+1}/{len(movie_chunks)})' if len(movie_chunks) > 1 else ''}",
            description="\n".join([f"• **{movie}**" for movie in chunk]),
            color=0x0099ff
        )
        embed.add_field(name="Usage", value="Use `/play <movie_name>` to start a watch party (with autocomplete!)", inline=False)
        embed.add_field(name="Total Movies", value=f"{len(movies)} movies available", inline=True)
        
        if i == 0:  # Only show this on first embed
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.followup.send(embed=embed)

@bot.tree.command(name="add_movie", description="Add a new movie from Google Drive")
async def add_movie(interaction: discord.Interaction, name: str, google_drive_url: str):
    """Add a new movie to the available list using Google Drive URL"""
    
    # Check if user has permission
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need administrator permissions to add movies!", ephemeral=True)
        return
    
    # Validate URL
    if "drive.google.com" not in google_drive_url and "docs.google.com" not in google_drive_url:
        await interaction.response.send_message("❌ Please provide a valid Google Drive URL!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    # Test if URL is accessible
    converted_url = movie_bot.convert_drive_url(google_drive_url)
    if not await movie_bot.verify_url(converted_url):
        embed = discord.Embed(
            title="⚠️ URL Not Accessible",
            description="The Google Drive link doesn't seem to be publicly accessible.",
            color=0xff9900
        )
        embed.add_field(
            name="How to fix:",
            value="1. Right-click the file in Google Drive\n2. Select 'Share'\n3. Change to 'Anyone with the link'\n4. Set permission to 'Viewer'\n5. Copy the link and try again",
            inline=False
        )
        await interaction.followup.send(embed=embed)
        return
    
    # Add movie to list
    movie_key = name.strip()
    movie_bot.movie_list[movie_key] = google_drive_url
    movie_bot.save_movie_list(movie_bot.movie_list)
    
    # Reload the movie list to ensure it's updated
    movie_bot.movie_list = movie_bot.load_movie_list()
    
    embed = discord.Embed(
        title="✅ Movie Added Successfully!",
        description=f"**{name}** has been added to the movie list.",
        color=0x00ff00
    )
    embed.add_field(name="Usage", value=f"Use `/play {name}` to start a watch party with this movie", inline=False)
    embed.add_field(name="Total Movies", value=f"{len(movie_bot.movie_list)} movies now available", inline=True)
    embed.add_field(name="Direct Link", value=f"[Watch in Browser]({converted_url})", inline=False)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="remove_movie", description="Remove a movie from the list")
@app_commands.autocomplete(name=movie_autocomplete)
async def remove_movie(interaction: discord.Interaction, name: str):
    """Remove a movie from the available list"""
    
    # Check if user has permission
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need administrator permissions to remove movies!", ephemeral=True)
        return
    
    # Find movie (case insensitive)
    movie_key = None
    for key in movie_bot.movie_list.keys():
        if key.lower() == name.lower():
            movie_key = key
            break
    
    if not movie_key:
        await interaction.response.send_message(f"❌ Movie '{name}' not found in the list!", ephemeral=True)
        return
    
    del movie_bot.movie_list[movie_key]
    movie_bot.save_movie_list(movie_bot.movie_list)
    
    await interaction.response.send_message(f"✅ Removed movie '{movie_key}' from the list!")

@bot.tree.command(name="movie_info", description="Get information about a specific movie")
@app_commands.autocomplete(movie=movie_autocomplete)
async def movie_info(interaction: discord.Interaction, movie: str):
    """Get information about a specific movie"""
    
    # Find movie URL
    movie_url = None
    movie_name = None
    for key, url in movie_bot.movie_list.items():
        if key.lower().replace(" ", "").replace("-", "").replace("_", "") == movie.lower().replace(" ", "").replace("-", "").replace("_", ""):
            movie_url = url
            movie_name = key
            break
    
    if not movie_url:
        await interaction.response.send_message(f"❌ Movie '{movie}' not found!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"🎬 {movie_name}",
        description="Movie Information",
        color=0x0099ff
    )
    embed.add_field(name="Google Drive URL", value=f"[View File]({movie_url})", inline=False)
    embed.add_field(name="Direct Stream URL", value=f"[Direct Link]({movie_bot.convert_drive_url(movie_url)})", inline=False)
    embed.add_field(name="Play Command", value=f"`/play {movie_name}` - Start watch party", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="Show bot help and setup instructions")
async def help_command(interaction: discord.Interaction):
    """Show help information"""
    
    embed = discord.Embed(
        title="🎬 Discord Movie Bot Help",
        description="Start movie watch parties with friends in Discord voice channels!",
        color=0x0099ff
    )
    
    embed.add_field(
        name="🎵 Basic Commands",
        value="`/play <movie>` - Start a watch party (with autocomplete!)\n`/stop` - End watch party and leave voice\n`/movies` - List available movies",
        inline=False
    )
    
    embed.add_field(
        name="🔧 Admin Commands",
        value="`/add_movie <n> <url>` - Add movie\n`/remove_movie <n>` - Remove movie\n`/movie_info <n>` - Movie details",
        inline=False
    )
    
    embed.add_field(
        name="📁 Adding Movies from Google Drive",
        value="1. Upload video to Google Drive\n2. Right-click → Share → 'Anyone with link'\n3. Copy the share URL\n4. Use `/add_movie MovieName <URL>`",
        inline=False
    )
    
    embed.add_field(
        name="🍿 How Watch Parties Work",
        value="1. Use `/play MovieName` while in a voice channel\n2. Bot joins and provides movie link\n3. Everyone clicks the link to watch\n4. Stay in voice to chat during the movie!\n5. Use `/stop` when done",
        inline=False
    )
    
    embed.add_field(
        name="✨ Features",
        value="• **No FFmpeg needed**: Direct Google Drive links\n• **Watch together**: Everyone gets the same link\n• **Voice chat**: Perfect for reactions and commentary\n• **Auto-cleanup**: Bot leaves when channel is empty\n• **Autocomplete**: Easy movie selection",
        inline=False
    )
    
    embed.add_field(
        name="⚠️ Requirements",
        value="• Files must be publicly accessible on Google Drive\n• Everyone needs to click the provided link\n• Coordinate play/pause through voice chat\n• Works with MP4, MKV, AVI, etc.",
        inline=False
    )
    
    embed.set_footer(text="Perfect for movie nights with friends! 🎭")
    
    await interaction.response.send_message(embed=embed)

@bot.event
async def on_voice_state_update(member, before, after):
    """Handle voice state updates - auto disconnect when alone"""
    if member == bot.user:
        return
    
    guild_id = member.guild.id
    if guild_id not in movie_bot.current_streams:
        return
    
    voice_client = movie_bot.current_streams[guild_id]
    if voice_client and voice_client.channel:
        # Count non-bot members in voice channel
        human_members = [m for m in voice_client.channel.members if not m.bot]
        
        if len(human_members) == 0:
            # No humans left, disconnect after delay
            await asyncio.sleep(30)  # 30 second delay
            
            # Check again after delay
            if voice_client.channel and guild_id in movie_bot.current_streams:
                human_members = [m for m in voice_client.channel.members if not m.bot]
                if len(human_members) == 0:
                    await voice_client.disconnect()
                    if guild_id in movie_bot.current_streams:
                        del movie_bot.current_streams[guild_id]
                    logger.info(f"Auto-disconnected from empty voice channel in guild {guild_id}")

# Error handling
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    logger.error(f"Command error: {error}")
    
    error_msg = "❌ An error occurred!"
    
    if "HTTP" in str(error):
        error_msg = "❌ Network error! Check if the Google Drive link is accessible."
    elif "permission" in str(error).lower():
        error_msg = "❌ Permission error! Make sure the Google Drive file is shared publicly."
    
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(error_msg, ephemeral=True)
        else:
            await interaction.followup.send(error_msg, ephemeral=True)
    except:
        pass

if __name__ == "__main__":
    # Run the bot
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("Please set the DISCORD_TOKEN environment variable!")
    else:
        logger.info("Starting Discord Movie Bot...")
        bot.run(BOT_TOKEN)
