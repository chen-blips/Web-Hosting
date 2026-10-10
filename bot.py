import io
import os
import asyncio
import discord
import requests  
import boto3  # Connects directly to Cloudflare R2
from discord.ext import commands
from dotenv import load_dotenv
import chat_exporter

# --- RENDER WEB SERVICE KEEP-ALIVE SYSTEM ---
# This tiny server tricks Render into seeing a live website, forcing the build to complete instantly!
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.getenv('PORT', 10000))), type('H', (BaseHTTPRequestHandler,), {'do_GET': lambda s: s.send_response(200) or s.end_headers() or s.wfile.write(b'OK')})).serve_forever(), daemon=True).start()

# Load environment variables from Render's cloud settings
load_dotenv('token.env')
TOKEN = os.getenv('DISCORD_TOKEN')
R2_ACCOUNT_ID = os.getenv('R2_ACCOUNT_ID')
R2_ACCESS_KEY_ID = os.getenv('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.getenv('R2_SECRET_ACCESS_KEY')
R2_BUCKET = os.getenv('R2_BUCKET')
R2_PUBLIC_DOMAIN = os.getenv('R2_PUBLIC_DOMAIN') 
TRANSCRIPT_DESTINATION_CHANNEL_ID = int(os.getenv('TRANSCRIPT_DESTINATION_CHANNEL_ID', '1558152299082158151'))

# Validate your environment variables are configured
if not all((R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET, R2_PUBLIC_DOMAIN)):
    raise ValueError("Cloudflare R2 credentials and R2_PUBLIC_DOMAIN must be configured in Render Settings!")

# Establish your permanent Cloudflare Connection Client
r2 = boto3.client(
    "s3",
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    region_name="auto",
)

# Enable required intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

bot = commands.Bot(command_prefix='!', intents=intents)

if not TOKEN:
    raise ValueError("DISCORD_TOKEN was not found. Verify your environment variables!")

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} | Cloudflare R2 Connection Established Safely.')

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.author.bot:
        if "generate transcript" in message.content.lower():
            await create_and_send_transcript(message.channel)
            return

        if message.embeds:
            for embed in message.embeds:
                if embed.description and "generate transcript" in embed.description.lower():
                    await create_and_send_transcript(message.channel)
                    return

    await bot.process_commands(message)


# Stylized link button to let all staff open browser windows natively
class BrowserTranscriptView(discord.ui.View):
    def __init__(self, url: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="👉 Open Transcript Link", url=url, style=discord.ButtonStyle.link))


async def create_and_send_transcript(channel):
    """Compile history instantly, sync directly to Cloudflare R2, and dispatch button card"""
    status_msg = await channel.send("🔄 *Wait... Compiling channel logs...*")

    try:
        # SPEED OPTIMIZATION: Only reads recent buyer/staff chatter (Fast generation!)
        # You can change limit=100 to limit=None if you want entire text histories instead
        transcript_html = await chat_exporter.export(channel, limit=100)
        if transcript_html is None:
            await status_msg.edit(content="❌ Failed to generate logs.")
            return

        object_key = f"transcripts/{channel.id}/{status_msg.id}.html"
        
        # Execute boto3 client upload within a worker thread to prevent lagging
        await asyncio.to_thread(
            upload_transcript_to_r2,
            transcript_html,
            object_key,
        )

        # Build your permanent public Cloudflare web URL link
        attachment_url = f"https://{R2_PUBLIC_DOMAIN}/{object_key}"

        # Clean up temporary status message text
        await status_msg.delete()
        
        # Format the crisp final embed summary layout
        embed = discord.Embed(
            title=f"📄 Transcript Ready: #{channel.name}", 
            description="The conversation history has been archived permanently to secure cloud frames. Click below to view it:",
            color=discord.Color.green()
        )
        embed.add_field(name="Source Workspace", value=channel.mention, inline=True)
        
        # Lookup your specific destination log room
        destination_channel = bot.get_channel(TRANSCRIPT_DESTINATION_CHANNEL_ID)
        if destination_channel is None:
            destination_channel = await bot.fetch_channel(TRANSCRIPT_DESTINATION_CHANNEL_ID)

        # Dispatch card directly to your target logging space channel
        await destination_channel.send(embed=embed, view=BrowserTranscriptView(url=attachment_url))
            
    except Exception as e:
        print(f"Error handling transcript: {e}")
        await channel.send(content="❌ An error occurred while uploading log frames to Cloudflare R2.")

def upload_transcript_to_r2(transcript_html: str, object_key: str):
    """Worker sync function to stream files directly into your public bucket"""
    r2.put_object(
        Bucket=R2_BUCKET,
        Key=object_key,
        Body=transcript_html.encode("utf-8"),
        ContentType="text/html; charset=utf-8",
        ContentDisposition="inline", # Forces browser tabs to render it as a website immediately!
    )

@bot.command()
async def transcript(ctx):
    """Manual override command (!transcript)"""
    await create_and_send_transcript(ctx.channel)

bot.run(TOKEN)
