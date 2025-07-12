# container-discord-bot 

This image allows an authorized user to control a docker daemon from the comfort of a Discord text channel.

It allows (for now) to restart containers, see their logs and check their general status.

## compose.yml example
```yaml
services:
  container-discord-bot:
    image: henderson43/container-discord-bot:latest
    container_name: container-discord-bot
    restart: unless-stopped
    environment:
      - DISCORD_TOKEN=${DISCORD_TOKEN}
      - DISCORD_GUILD_ID=${DISCORD_GUILD_ID}
      - DISCORD_CHANNEL_ID=${DISCORD_CHANNEL_ID}
      - AUTHORIZED_USERS=${AUTHORIZED_USERS}
      - CONTAINER_NOTIFICATIONS_ENABLED=true
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
```
## .env example
```bash
DISCORD_TOKEN="your_discord_bot_token" #string
DISCORD_GUILD_ID=your_server_id #int
DISCORD_CHANNEL_ID=your_channel_id #int
AUTHORIZED_USERS=user_id1,user_id2,user_id3,...,user_idN #[int]
CONTAINER_NOTIFICATIONS_ENABLED= flag #bool

```

# Commands

## Prefix
The bot uses a '$' prefix for its commands

## Command list
- $logs {container} [lines_count] - Obtain lines_count lines of logs from container (default: 50 lines, max: 2000 lines)
- $restart {container} - Restart container
- $status [container] - Show status of all containers or a specific one
- $toggle_notifications - Turns on/off notifications systems for docker events (kill, start, die)
- $help - Prints the command list with a short summary of their purpose
