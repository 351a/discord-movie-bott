import discord
from discord.ext import commands
import asyncio
import os
import json
from typing import Dict, List
import aiohttp
import subprocess
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
# Movie files should be in the same directory as your bot or in a 'movies' folder
MOVIES_FOLDER = "./movies/"  # Local folder for movie files
MOVIE_LIST_FILE = "movies.json"  # Local file containing movie list and URLs

class MovieBot:
    def __init__(self):
        self.current_streams: Dict[int, subprocess.Popen] = {}  # guild_id -> process
        self.movie_list = self.load_movie_list()
    
    def load_movie_list(self) -> Dict[str, str]:
        """Load movie list from JSON file or scan movies folder"""
        try:
            with open(MOVIE_LIST_FILE, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            # Scan movies folder for files
            movie_dict = {}
            if os.path.exists(MOVIES_FOLDER):
                for filename in os.listdir(MOVIES_FOLDER):
                    if filename.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv')):
                        movie_name = os.path.splitext(filename)[0].lower().replace(" ", "")
                        movie_path = os.path.join(MOVIES_FOLDER, filename)
                        movie_dict[movie_name] = movie_path
            else:
                # Create movies folder and default structure
                os.makedirs(MOVIES_FOLDER, exist_ok=True)
                logger.info(f"Created movies folder: {MOVIES_FOLDER}")
                logger.info("Please upload your movie files to the movies folder!")
            
            self.save_movie_list(movie_dict)
            return movie_dict
    
    def save_movie_list(self, movie_list: Dict[str, str]):
        """Save movie list to JSON file"""
        with open(MOVIE_LIST_FILE, 'w') as f:
            json.dump(movie_list, f, indent=2)
    
    def get_movie_url(self, movie_name: str) -> str:
        """Get movie URL by name (case insensitive)"""
        movie_name = movie_name.lower().replace(" ", "")
        return self.movie_list.get(movie_name)
    
    def list_movies(self) -> List[str]:
        """Get list of available movies"""
        return list(self.movie_list.keys())

movie_bot = MovieBot()

@bot.event
async def on_ready():
    logger.info(f'{bot.user} has connected to Discord!')
    
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")

@bot.tree.command(name="play", description="Play a movie in voice channel")
async def play_movie(interaction: discord.Interaction, movie: str):
    """Play a movie in the user's voice channel"""
    
    # Check if user is in a voice channel
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("❌ You need to be in a voice channel to use this command!", ephemeral=True)
        return
    
    # Check if movie file exists locally
    movie_path = movie_bot.get_movie_url(movie)
    if not movie_path:
        available_movies = ", ".join(movie_bot.list_movies())
        await interaction.response.send_message(
            f"❌ Movie '{movie}' not found!\n**Available movies:** {available_movies}", 
            ephemeral=True
        )
        return
    
    # Verify file exists
    if not os.path.exists(movie_path):
        await interaction.response.send_message(f"❌ Movie file not found: {movie_path}", ephemeral=True)
        return
    
    # Check if bot is already streaming in this guild
    guild_id = interaction.guild_id
    if guild_id in movie_bot.current_streams:
        await interaction.response.send_message("❌ Already streaming a movie in this server! Use `/stop` first.", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        # Join voice channel
        voice_channel = interaction.user.voice.channel
        voice_client = await voice_channel.connect()
        
        # Start streaming using FFmpeg with local file
        ffmpeg_options = {
            'before_options': '-re',  # Read input at native frame rate
            'options': '-vn -bufsize 64k'  # No video, audio only with buffer
        }
        
        # Create audio source from local file
        audio_source = discord.FFmpegPCMAudio(movie_path, **ffmpeg_options)
        
        # Start playing
        voice_client.play(audio_source, after=lambda e: logger.error(f'Player error: {e}') if e else None)
        
        # Store the voice client reference
        movie_bot.current_streams[guild_id] = voice_client
        
        embed = discord.Embed(
            title="🎬 Now Playing",
            description=f"**Movie:** {movie.title()}\n**Channel:** {voice_channel.name}",
            color=0x00ff00
        )
        embed.add_field(name="Controls", value="Use `/stop` to stop playback", inline=False)
        
        await interaction.followup.send(embed=embed)
        
        # Auto-disconnect when finished
        while voice_client.is_playing() or voice_client.is_paused():
            await asyncio.sleep(1)
        
        # Clean up
        if guild_id in movie_bot.current_streams:
            del movie_bot.current_streams[guild_id]
        await voice_client.disconnect()
        
    except Exception as e:
        logger.error(f"Error playing movie: {e}")
        if guild_id in movie_bot.current_streams:
            del movie_bot.current_streams[guild_id]
        try:
            await voice_client.disconnect()
        except:
            pass
        await interaction.followup.send(f"❌ Error playing movie: {str(e)}")

@bot.tree.command(name="stop", description="Stop current movie playback")
async def stop_movie(interaction: discord.Interaction):
    """Stop current movie playbook"""
    guild_id = interaction.guild_id
    
    if guild_id not in movie_bot.current_streams:
        await interaction.response.send_message("❌ No movie is currently playing!", ephemeral=True)
        return
    
    try:
        voice_client = movie_bot.current_streams[guild_id]
        voice_client.stop()
        await voice_client.disconnect()
        del movie_bot.current_streams[guild_id]
        
        await interaction.response.send_message("⏹️ Movie playback stopped!")
        
    except Exception as e:
        logger.error(f"Error stopping movie: {e}")
        await interaction.response.send_message(f"❌ Error stopping movie: {str(e)}")

@bot.tree.command(name="movies", description="List available movies")
async def list_movies(interaction: discord.Interaction):
    """List all available movies"""
    movies = movie_bot.list_movies()
    
    if not movies:
        await interaction.response.send_message("❌ No movies available!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🎬 Available Movies",
        description="\n".join([f"• {movie.title()}" for movie in movies]),
        color=0x0099ff
    )
    embed.add_field(name="Usage", value="Use `/play <movie_name>` to play a movie", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="add_movie", description="Add a new movie file")
async def add_movie(interaction: discord.Interaction, name: str, filename: str):
    """Add a new movie to the available list by filename"""
    
    # Check if user has permission (you can modify this check)
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need administrator permissions to add movies!", ephemeral=True)
        return
    
    # Check if file exists in movies folder
    file_path = os.path.join(MOVIES_FOLDER, filename)
    if not os.path.exists(file_path):
        await interaction.response.send_message(f"❌ File '{filename}' not found in movies folder!", ephemeral=True)
        return
    
    # Add movie to list
    movie_key = name.lower().replace(" ", "")
    movie_bot.movie_list[movie_key] = file_path
    movie_bot.save_movie_list(movie_bot.movie_list)
    
    await interaction.response.send_message(f"✅ Added movie '{name}' (file: {filename}) to the list!")

@bot.tree.command(name="scan_movies", description="Scan movies folder for new files")
async def scan_movies(interaction: discord.Interaction):
    """Scan the movies folder and update the movie list"""
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need administrator permissions to scan movies!", ephemeral=True)
        return
    
    if not os.path.exists(MOVIES_FOLDER):
        await interaction.response.send_message("❌ Movies folder not found!", ephemeral=True)
        return
    
    # Scan for movie files
    new_movies = []
    for filename in os.listdir(MOVIES_FOLDER):
        if filename.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv')):
            movie_name = os.path.splitext(filename)[0].lower().replace(" ", "")
            movie_path = os.path.join(MOVIES_FOLDER, filename)
            
            if movie_name not in movie_bot.movie_list:
                movie_bot.movie_list[movie_name] = movie_path
                new_movies.append(filename)
    
    # Save updated list
    movie_bot.save_movie_list(movie_bot.movie_list)
    
    if new_movies:
        movie_list = "\n".join([f"• {movie}" for movie in new_movies])
        await interaction.response.send_message(f"✅ Found {len(new_movies)} new movies:\n{movie_list}")
    else:
        await interaction.response.send_message("ℹ️ No new movies found in the folder.")

@bot.tree.command(name="upload_info", description="Get information about uploading movies")
async def upload_info(interaction: discord.Interaction):
    """Provide information about uploading movie files"""
    
    embed = discord.Embed(
        title="📁 How to Upload Movies",
        description="Instructions for adding movies to the bot",
        color=0x0099ff
    )
    
    embed.add_field(
        name="1. Upload Files",
        value=f"Upload your movie files to the `{MOVIES_FOLDER}` folder on the server",
        inline=False
    )
    
    embed.add_field(
        name="2. Supported Formats",
        value="MP4, MKV, AVI, MOV, WMV, FLV",
        inline=False
    )
    
    embed.add_field(
        name="3. Scan for New Files",
        value="Use `/scan_movies` to detect newly uploaded files",
        inline=False
    )
    
    embed.add_field(
        name="4. Manual Addition",
        value="Use `/add_movie <name> <filename>` to manually add specific files",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="remove_movie", description="Remove a movie from the list")
async def remove_movie(interaction: discord.Interaction, name: str):
    """Remove a movie from the available list"""
    
    # Check if user has permission
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need administrator permissions to remove movies!", ephemeral=True)
        return
    
    movie_key = name.lower().replace(" ", "")
    
    if movie_key not in movie_bot.movie_list:
        await interaction.response.send_message(f"❌ Movie '{name}' not found in the list!", ephemeral=True)
        return
    
    del movie_bot.movie_list[movie_key]
    movie_bot.save_movie_list(movie_bot.movie_list)
    
    await interaction.response.send_message(f"✅ Removed movie '{name}' from the list!")

@bot.event
async def on_voice_state_update(member, before, after):
    """Handle voice state updates"""
    # Auto-disconnect if bot is alone in voice channel
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
            if voice_client.channel:
                human_members = [m for m in voice_client.channel.members if not m.bot]
                if len(human_members) == 0:
                    voice_client.stop()
                    await voice_client.disconnect()
                    if guild_id in movie_bot.current_streams:
                        del movie_bot.current_streams[guild_id]

# Error handling
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    logger.error(f"Command error: {error}")
    if not interaction.response.is_done():
        await interaction.response.send_message(f"❌ An error occurred: {str(error)}", ephemeral=True)
    else:
        await interaction.followup.send(f"❌ An error occurred: {str(error)}", ephemeral=True)

if __name__ == "__main__":
    # Run the bot
    bot.run(BOT_TOKEN)
