import discord
from discord.ext import commands
import docker
import os
import asyncio
from datetime import datetime
import logging

# Configurazione logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configurazione
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = int(os.getenv('GUILD_ID', '0'))
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
AUTHORIZED_USERS = os.getenv('AUTHORIZED_USERS', '').split(',')

# Inizializza il client Docker
try:
    docker_client = docker.from_env()
    logger.info("Connesso al Docker daemon")
except Exception as e:
    logger.error(f"Errore connessione Docker: {e}")
    exit(1)

# Configura il bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

def is_authorized(user_id):
    """Verifica se l'utente è autorizzato ad eseguire comandi"""
    return str(user_id) in AUTHORIZED_USERS or not AUTHORIZED_USERS

def get_container_by_name(name):
    """Ottiene un container per nome"""
    try:
        return docker_client.containers.get(name)
    except docker.errors.NotFound:
        return None

def format_logs(logs, lines=50):
    """Formatta i log per Discord (max 2000 caratteri per messaggio)"""
    if not logs:
        return ["Nessun log disponibile"]
    
    # Prendi solo le ultime righe
    log_lines = logs.strip().split('\n')
    if len(log_lines) > lines:
        log_lines = log_lines[-lines:]
    
    # Dividi in chunk per rispettare il limite di Discord
    chunks = []
    current_chunk = "```\n"
    
    for line in log_lines:
        # Se aggiungere questa riga supera il limite, inizia un nuovo chunk
        if len(current_chunk) + len(line) + 10 > 1990:  # 10 caratteri di margine
            current_chunk += "```"
            chunks.append(current_chunk)
            current_chunk = "```\n"
        
        current_chunk += line + "\n"
    
    if current_chunk != "```\n":
        current_chunk += "```"
        chunks.append(current_chunk)
    
    return chunks

@bot.event
async def on_ready():
    logger.info(f'{bot.user} è connesso a Discord!')
    
    # Verifica la connessione al server e canale
    guild = bot.get_guild(GUILD_ID)
    if guild:
        channel = guild.get_channel(CHANNEL_ID)
        if channel:
            await channel.send(f"🤖 Bot avviato! Connesso a Docker daemon.")
        else:
            logger.warning(f"Canale {CHANNEL_ID} non trovato")
    else:
        logger.warning(f"Server {GUILD_ID} non trovato")

@bot.command(name='logs')
async def get_logs(ctx, container_name: str, lines: int = 50):
    """
    Ottiene i log di un container
    Uso: !logs <nome_container> [numero_righe]
    """
    if not is_authorized(ctx.author.id):
        await ctx.reply("❌ Non sei autorizzato ad usare questo comando.")
        return
    
    if ctx.channel.id != CHANNEL_ID and CHANNEL_ID != 0:
        await ctx.reply(f"❌ Questo comando può essere usato solo nel canale designato.")
        return
    
    try:
        container = get_container_by_name(container_name)
        if not container:
            await ctx.reply(f"❌ Container '{container_name}' non trovato.")
            return
        
        # Invia messaggio di caricamento
        loading_msg = await ctx.reply(f"📋 Recupero log di '{container_name}'...")
        
        # Ottieni i log
        logs = container.logs(tail=lines, timestamps=True).decode('utf-8')
        
        # Formatta e invia i log
        log_chunks = format_logs(logs, lines)
        
        await loading_msg.edit(content=f"📋 **Log di '{container_name}' (ultime {lines} righe):**")
        
        for chunk in log_chunks:
            await ctx.send(chunk)
            
    except Exception as e:
        logger.error(f"Errore nel recupero log: {e}")
        await ctx.reply(f"❌ Errore nel recupero dei log: {str(e)}")

@bot.command(name='restart')
async def restart_container(ctx, container_name: str):
    """
    Riavvia un container
    Uso: !restart <nome_container>
    """
    if not is_authorized(ctx.author.id):
        await ctx.reply("❌ Non sei autorizzato ad usare questo comando.")
        return
    
    if ctx.channel.id != CHANNEL_ID and CHANNEL_ID != 0:
        await ctx.reply(f"❌ Questo comando può essere usato solo nel canale designato.")
        return
    
    try:
        container = get_container_by_name(container_name)
        if not container:
            await ctx.reply(f"❌ Container '{container_name}' non trovato.")
            return
        
        # Invia messaggio di conferma
        loading_msg = await ctx.reply(f"🔄 Riavvio del container '{container_name}' in corso...")
        
        # Riavvia il container
        container.restart()
        
        # Aspetta che il container sia effettivamente riavviato
        await asyncio.sleep(3)
        container.reload()
        
        status = container.status
        if status == 'running':
            await loading_msg.edit(content=f"✅ Container '{container_name}' riavviato con successo!")
        else:
            await loading_msg.edit(content=f"⚠️ Container '{container_name}' riavviato ma status: {status}")
            
    except Exception as e:
        logger.error(f"Errore nel riavvio container: {e}")
        await ctx.reply(f"❌ Errore nel riavvio del container: {str(e)}")

@bot.command(name='status')
async def container_status(ctx, container_name: str = None):
    """
    Mostra lo status dei container
    Uso: !status [nome_container]
    """
    if not is_authorized(ctx.author.id):
        await ctx.reply("❌ Non sei autorizzato ad usare questo comando.")
        return
    
    if ctx.channel.id != CHANNEL_ID and CHANNEL_ID != 0:
        await ctx.reply(f"❌ Questo comando può essere usato solo nel canale designato.")
        return
    
    try:
        if container_name:
            # Status di un container specifico
            container = get_container_by_name(container_name)
            if not container:
                await ctx.reply(f"❌ Container '{container_name}' non trovato.")
                return
            
            container.reload()
            status = container.status
            created = container.attrs['Created'][:19]  # Prendi solo data e ora
            
            embed = discord.Embed(
                title=f"Status Container: {container_name}",
                color=discord.Color.green() if status == 'running' else discord.Color.red()
            )
            embed.add_field(name="Status", value=status, inline=True)
            embed.add_field(name="Creato", value=created, inline=True)
            
            if status == 'running':
                embed.add_field(name="Uptime", value="In esecuzione", inline=True)
            
            await ctx.reply(embed=embed)
        else:
            # Status di tutti i container
            containers = docker_client.containers.list(all=True)
            
            if not containers:
                await ctx.reply("📋 Nessun container trovato.")
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
                embed.add_field(name="In esecuzione", value="\n".join(running), inline=False)
            if stopped:
                embed.add_field(name="Fermati", value="\n".join(stopped), inline=False)
            
            await ctx.reply(embed=embed)
            
    except Exception as e:
        logger.error(f"Errore nel recupero status: {e}")
        await ctx.reply(f"❌ Errore nel recupero dello status: {str(e)}")

@bot.command(name='help_container')
async def help_container(ctx):
    """Mostra l'aiuto per i comandi del bot"""
    if not is_authorized(ctx.author.id):
        await ctx.reply("❌ Non sei autorizzato ad usare questo comando.")
        return
    
    embed = discord.Embed(
        title="🤖 Comandi Bot Container",
        description="Comandi disponibili per la gestione dei container Docker",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="!logs <container> [righe]",
        value="Ottiene i log di un container (default: 50 righe)",
        inline=False
    )
    
    embed.add_field(
        name="!restart <container>",
        value="Riavvia un container",
        inline=False
    )
    
    embed.add_field(
        name="!status [container]",
        value="Mostra lo status di un container specifico o di tutti",
        inline=False
    )
    
    embed.add_field(
        name="!help_container",
        value="Mostra questo messaggio di aiuto",
        inline=False
    )
    
    await ctx.reply(embed=embed)

# Gestione errori
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return  # Ignora comandi non trovati
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"❌ Argomento mancante. Usa `!help_container` per vedere la sintassi.")
    else:
        logger.error(f"Errore comando: {error}")
        await ctx.reply(f"❌ Si è verificato un errore: {str(error)}")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN non configurato!")
        exit(1)
    
    bot.run(DISCORD_TOKEN)