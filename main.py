import discord
from discord.ext import commands
import docker
import os
import asyncio
from datetime import datetime
import logging
import queue
import threading
from difflib import get_close_matches
import io

# Configurazione logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configurazione
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = int(os.getenv('DISCORD_GUILD_ID', '0'))
CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID', '0'))
AUTHORIZED_USERS = os.getenv('AUTHORIZED_USERS', '').split(',')
CONTAINER_EVENTS_ENABLED = os.getenv('CONTAINER_EVENTS_ENABLED', 'false').lower() in ['true', '1']
MONITORED_CONTAINERS = os.getenv('MONITORED_CONTAINERS','')

if MONITORED_CONTAINERS == '':
    MONITORED_CONTAINERS = []
else:
    MONITORED_CONTAINERS = [c.strip() for c in MONITORED_CONTAINERS.split(',') if c.strip()]

logger.info(f"MONITORED_CONTAINERS: {MONITORED_CONTAINERS}")
# Inizializza il client Docker
try:
    docker_client = docker.from_env()
    logger.info("Connected to docker daemon")
except Exception as e:
    logger.error(f"Docker connection error: {e}")
    exit(1)

def is_authorized(user_id):
    """Verifica se l'utente è autorizzato ad eseguire comandi"""
    return str(user_id) in AUTHORIZED_USERS or not AUTHORIZED_USERS

def get_container_by_name(name):
    """Ottiene un container per nome"""
    try:
        return docker_client.containers.get(name)
    except docker.errors.NotFound:
        return None

def create_log_file(logs: str, filename_base: str):
    """Create a single discord.File from logs (plain text) named <base>_YYYYMMDD_HHMMSS.log"""
    if not logs:
        logs = "No logs available.\n"
    bio = io.BytesIO(logs.encode('utf-8'))
    bio.seek(0)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{filename_base}_{timestamp}.log"
    return discord.File(fp=bio, filename=filename)

container_event_task = None

event_queue = queue.Queue()

def docker_event_thread():
    """Thread that monitors Docker events and puts them in a queue"""
    try:
        for event in docker_client.events(decode=True):
            if CONTAINER_EVENTS_ENABLED and event['Type'] == 'container':
                event_queue.put(event)
    except Exception as e:
        logger.error(f"Error in Docker events thread: {e}")

async def container_event_worker(channel):
    """Worker that processes events from the queue"""
    # Start the Docker events thread
    docker_thread = threading.Thread(target=docker_event_thread, daemon=True)
    docker_thread.start()
    
    while True:
        try:
            # Check for events in the queue (non-blocking)
            try:
                event = event_queue.get_nowait()
                
                container_name = event['Actor']['Attributes'].get('name', 'unknown')
                if MONITORED_CONTAINERS and container_name not in MONITORED_CONTAINERS:
                    continue  # Skip non-monitored containers
                action = event['Action']
                if action not in ['start', 'die']:
                    continue  # Only process relevant actions
                time = datetime.fromtimestamp(event['time']).strftime('%Y-%m-%d %H:%M:%S')
                
                message = f"🛠️ **Container Event**: `{container_name}` {action} at {time}"
                
                logger.info(message)
                await channel.send(message)

                if action == 'die':
                    # collect last lines of logs and send as a single .log attachment
                    container = get_container_by_name(container_name)
                    if container:
                        logs = container.logs(tail=25, timestamps=True).decode('utf-8')
                        file = create_log_file(logs, container_name)
                        await channel.send(file=file)
                    else:
                        await channel.send(f"❌ Container '{container_name}' not found for logs.")
                
            except queue.Empty:
                # No events in queue, sleep a bit
                await asyncio.sleep(0.5)
                
        except Exception as e:
            logger.error(f"Error processing Docker events: {e}")
            await asyncio.sleep(1)


#==========================
# DISCORD bot configuration
#==========================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='$', intents=intents, help_command=None)

def check_user_authorization(user_id):
    if not is_authorized(user_id):
        raise Exception("You are not authorized to use this command.")

def check_discord_channel_authorization(channel_id):
    if channel_id != CHANNEL_ID and CHANNEL_ID != 0:
        raise Exception(f"This command cannot be used in this channel.")

def check_container_authorization(container_name):
    if MONITORED_CONTAINERS and container_name not in MONITORED_CONTAINERS:
        raise Exception(f"Container '{container_name}' is not monitored by this bot.")

def check_authorizations(ctx, container_name=None):
    if not is_authorized(ctx.author.id):
        raise Exception("You are not authorized to use this command.")
    if ctx.channel.id != CHANNEL_ID and CHANNEL_ID != 0:
        raise Exception(f"This command cannot be used in this channel.")
    if container_name and MONITORED_CONTAINERS and container_name not in MONITORED_CONTAINERS:
        raise Exception(f"Container '{container_name}' is not monitored by this bot.")

def offer_suggestion(container_name=None):
    # Get all available containers, filter through to leave only monitored ones if monitored list is set
    # Rank by similarity to the provided container_name
    all_containers = docker_client.containers.list(all=True)
    logger.info(f"All containers: {[c.name for c in all_containers]}")
    if MONITORED_CONTAINERS:
        all_containers = [c for c in all_containers if c.name in MONITORED_CONTAINERS]
        logger.info(f"Filtered containers: {[c.name for c in all_containers]}")
    container_names = [c.name for c in all_containers]
    logger.info(f"Container names for suggestions: {container_names}")
    suggestions = get_close_matches(container_name, container_names, n=3, cutoff=0.1)
    logger.info(f"Suggestions for '{container_name}': {suggestions}")
    return suggestions

@bot.event
async def on_ready():
    logger.info(f'{bot.user} connected to Discord!')
    
    # Verifica la connessione al server e canale
    guild = bot.get_guild(GUILD_ID)
    if guild:
        channel = guild.get_channel(CHANNEL_ID)
        if channel:
            await channel.send(f"🤖 Container monitor bot started")
            # Avvia il task per gli eventi dei container
            container_event_task = asyncio.create_task(container_event_worker(channel))
            await channel.send(f"🤖 Connected to Docker daemon and ready to monitor containers!")
        else:
            logger.warning(f"Channel {CHANNEL_ID} not found")
    else:
        logger.warning(f"Server {GUILD_ID} not found")

@bot.command(name='toggle_notifications')
async def toggle_notifications(ctx):
    """
    Toggle container event notifications
    Usage: $toggle_notifications
    """

    logger.info(f"Toggle notifications command invoked by {ctx.author.name} ({ctx.author.id})")
    try:
        check_user_authorization(ctx.author.id)
        check_discord_channel_authorization(ctx.channel.id)
        
        global CONTAINER_EVENTS_ENABLED
        CONTAINER_EVENTS_ENABLED = not CONTAINER_EVENTS_ENABLED
        
        status = "enabled" if CONTAINER_EVENTS_ENABLED else "disabled"
        logger.info(f"Container event notifications {status} by {ctx.author.name} ({ctx.author.id})")
        await ctx.reply(f"🔔 Container event notifications are now {status}.")
    except Exception as e:
        logger.error(f"Exception caught in toggle_notifications: {e}")
        await ctx.reply(f"❌ Error: {str(e)}")

@bot.command(name='logs')
async def get_logs(ctx, container_name: str, lines: int = 50):
    """
    Obtain logs from a container
    Usage: $logs <container_name> [lines_count] (default: 50 lines)
    """
    logger.info(f"Logs command invoked by {ctx.author.name} ({ctx.author.id}) for container '{container_name}' with {lines} lines")
    try:
        check_user_authorization(ctx.author.id)
        check_discord_channel_authorization(ctx.channel.id)
        container = get_container_by_name(container_name)
        if not container:
            await ctx.reply(f"❌ Container '{container_name}' not found.")
            suggestions = offer_suggestion(container_name)
            if suggestions and len(suggestions) > 0:
                await ctx.reply(f"Did you mean: {suggestions[0]}?")
            return
        check_container_authorization(container_name)
        # Invia messaggio di caricamento
        loading_msg = await ctx.reply(f"📋 Retrieving logs for '{container_name}'...")
        
        # Ottieni i log
        logs = container.logs(tail=lines, timestamps=True).decode('utf-8')
        
        # Create single .log attachment and send
        file = create_log_file(logs, container_name)
        
        await loading_msg.edit(content=f"📋 **'{container_name}' logs (last {lines} lines):**")
        await ctx.send(file=file)
            
    except Exception as e:
        logger.error(f"Exception caught in get_logs: {e}")
        await ctx.reply(f"❌ Error: {str(e)}")

@bot.command(name='restart')
async def restart_container(ctx, container_name: str):
    """
    Restart container
    Usage: $restart <container_name>
    """

    logger.info(f"Restart command invoked by {ctx.author.name} ({ctx.author.id}) for container '{container_name}'")
    
    try:
        check_user_authorization(ctx.author.id)
        check_discord_channel_authorization(ctx.channel.id)
        container = get_container_by_name(container_name)
        if not container:
            await ctx.reply(f"❌ Container '{container_name}' not found.")
            suggestions = offer_suggestion(container_name)
            if suggestions and len(suggestions) > 0:
                await ctx.reply(f"Did you mean: {suggestions[0]}?")
            return
        check_container_authorization(container_name)
        # Invia messaggio di conferma
        loading_msg = await ctx.reply(f"🔄 Restarting container '{container_name}'...")
        
        # Riavvia il container
        container.restart()
        
        # Aspetta che il container sia effettivamente riavviato
        await asyncio.sleep(3)
        container.reload()
        
        status = container.status
        if status == 'running':
            await loading_msg.edit(content=f"✅ Container '{container_name}' successfully restarted!")
        else:
            await loading_msg.edit(content=f"⚠️ Container '{container_name}' status: {status}")
            
    except Exception as e:
        logger.error(f"Exception caught in restart_container: {e}")
        await ctx.reply(f"❌ Error: {str(e)}")

@bot.command(name='status')
async def container_status(ctx, container_name: str = None):
    """
    Shows the status of all containers or a specific one
    Usage: $status [container_name]
    """
    logger.info(f"Status command invoked by {ctx.author.name} ({ctx.author.id}) for container '{container_name if container_name else 'all'}'")
    try:
        check_user_authorization(ctx.author.id)
        check_discord_channel_authorization(ctx.channel.id)
        if container_name:
            # Status di un container specifico
            container = get_container_by_name(container_name)
            if not container:
                await ctx.reply(f"❌ Container '{container_name}' not found.")
                suggestions = offer_suggestion(container_name)
                if suggestions and len(suggestions) > 0:
                    await ctx.reply(f"Did you mean: {suggestions[0]}?")
                return
            check_container_authorization(container_name)
            container.reload()
            status = container.status
            created = container.attrs['Created'][:19]  # Prendi solo data e ora
            embed = discord.Embed(
                title=f"Status Container: {container_name}",
                color=discord.Color.green() if status == 'running' else discord.Color.red()
            )
            embed.add_field(name="Status", value=status, inline=True)
            embed.add_field(name="Created", value=created, inline=True)
            
            await ctx.reply(embed=embed)
        else:
            # Status di tutti i container
            containers = []
            if MONITORED_CONTAINERS:
                for container_name in MONITORED_CONTAINERS:
                    logger.info(f"Checking status for container: {container_name}")
                    container = get_container_by_name(container_name)
                    if not container:
                        logger.warning(f"Container '{container_name}' not found")
                        continue
                    if container:
                        container.reload()
                        containers.append(container)
            else:
                containers = docker_client.containers.list(all=True)
                
            
            if not containers:
                await ctx.reply("📋 No container found.")
                return
            
            embed = discord.Embed(title="Status Container", color=discord.Color.blue())
            
            running = []
            stopped = []
            
            for container in containers:
                name = container.name
                status = container.status
                
                if status == 'running':
                    running.append(f"🟢 {name}")
                else:
                    stopped.append(f"🔴 {name} ({status})")
            
            if running:
                embed.add_field(name="Running", value="\n".join(running), inline=False)
            if stopped:
                embed.add_field(name="Stopped", value="\n".join(stopped), inline=False)
            
            await ctx.reply(embed=embed)
            
    except Exception as e:
        logger.error(f"Exception caught in container_status: {e}")
        await ctx.reply(f"❌ Error: {str(e)}")

@bot.command(name='help')
async def help_command(ctx):
    """
    Shows the help message with available commands
    Usage: $help
    """
    logger.info(f"Help command invoked by {ctx.author.name} ({ctx.author.id})")
    try:
        check_user_authorization(ctx.author.id)
        check_discord_channel_authorization(ctx.channel.id)
    
        help_text = (
            "🤖 **Container Monitor Bot Help**\n\n"
            "Available commands:\n"
            "`$status [container_name]` - Show status of all containers or a specific one.\n"
            "`$logs <container_name> [lines_count]` - Get logs from a container (default: 50 lines).\n"
            "`$restart <container_name>` - Restart a specific container.\n"
            "`$toggle_notifications` - Enable/disable container event notifications.\n"
            "`$help` - Show this help message."
        )
        
        await ctx.reply(help_text)
    except Exception as e:
        logger.error(f"Exception caught in help_command: {e}")
        await ctx.reply(f"❌ Error: {str(e)}")

# Gestione errori
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return  # Ignora comandi non trovati
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"❌ Missing argument. Use `$help` for the correct syntax.")
    else:
        logger.error(f"Error caught by on_command_error: {error}")
        await ctx.reply(f"❌ Error: {str(error)}")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN not configured!")
        exit(1)
    
    bot.run(DISCORD_TOKEN)