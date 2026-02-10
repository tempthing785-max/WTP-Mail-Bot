# ---------- FLASK SETUP ----------
from flask import Flask
from threading import Thread

app = Flask("")


@app.route("/")
def home():
    return "Bot is alive!"


def run():
    app.run(host="0.0.0.0", port=8080)


Thread(target=run).start()

# ---------- DISCORD BOT ----------

from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import io

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

CONFIG_FILE = "ticket_config.json"

# ---------- CONFIG HELPERS ----------

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)


def get_next_ticket_number(guild_id):
    config = load_config()
    guild_cfg = config.setdefault(str(guild_id), {})
    guild_cfg["ticket_counter"] = guild_cfg.get("ticket_counter", 0) + 1
    save_config(config)
    return guild_cfg["ticket_counter"]

# ---------- VIEWS ----------

class TicketTypeSelect(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="Select a ticket type...",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Support", description="General support", emoji="🛠️"),
            discord.SelectOption(label="Report", description="Report a user or issue", emoji="🚨"),
            discord.SelectOption(label="Appeal", description="Appeal a decision", emoji="⚖️"),
        ],
        custom_id="ticket_type_select"
    )
    async def select_ticket_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        ticket_type = select.values[0]
        guild = interaction.guild
        user = interaction.user
        config = load_config().get(str(guild.id))

        if not config:
            await interaction.response.send_message("Ticket system not configured.", ephemeral=True)
            return

        category = guild.get_channel(config["category_id"])
        mod_role = guild.get_role(config["mod_role_id"])
        admin_role = guild.get_role(config["admin_role_id"])

        # ---------- Prevent duplicate tickets ----------
        for ch in category.channels:
            if ch.topic == f"ticket_for:{user.id}":
                await interaction.response.send_message(
                    f"You already have a ticket open: {ch.mention}",
                    ephemeral=True
                )
                return

        ticket_number = get_next_ticket_number(guild.id)
        notify_role = admin_role if ticket_type == "Appeal" else mod_role

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            notify_role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True)
        }

        channel = await guild.create_text_channel(
            name=f"{ticket_type.lower()}-{ticket_number:04d}",
            category=category,
            overwrites=overwrites,
            topic=f"ticket_for:{user.id}"
        )

        embed = discord.Embed(
            title=f"🎟️ {ticket_type} Ticket #{ticket_number}",
            description="A staff member will assist you shortly.",
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"Ticket #{ticket_number}")

        await channel.send(
            content=f"{user.mention} {notify_role.mention}",
            embed=embed,
            view=TicketControlView(ticket_number, ticket_type)
        )

        await interaction.response.send_message(
            f"Your **{ticket_type}** ticket has been created: {channel.mention}",
            ephemeral=True
        )

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎟️ Open Ticket", style=discord.ButtonStyle.green, custom_id="ticket_open_button")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Please select your ticket type:",
            view=TicketTypeSelect(),
            ephemeral=True
        )

class TicketControlView(discord.ui.View):
    def __init__(self, ticket_number: int, ticket_type: str):
        super().__init__(timeout=None)
        self.ticket_number = ticket_number
        self.ticket_type = ticket_type

    def _has_staff_perms(self, interaction: discord.Interaction):
        config = load_config().get(str(interaction.guild.id))
        mod_role = interaction.guild.get_role(config["mod_role_id"])
        admin_role = interaction.guild.get_role(config["admin_role_id"])
        return mod_role in interaction.user.roles or admin_role in interaction.user.roles

    @discord.ui.button(label="🟢 Claim Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_claim_button")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._has_staff_perms(interaction):
            await interaction.response.send_message("Only staff can claim tickets.", ephemeral=True)
            return

        async for msg in interaction.channel.history(limit=10):
            if msg.embeds:
                embed = msg.embeds[0]
                if "Claimed by:" in embed.description:
                    await interaction.response.send_message("This ticket is already claimed.", ephemeral=True)
                    return

                embed.description += f"\n\n🟢 **Claimed by:** {interaction.user.mention}"
                await msg.edit(embed=embed, view=self)
                break

        await interaction.response.send_message("Ticket claimed!", ephemeral=True)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.red, custom_id="ticket_close_button")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._has_staff_perms(interaction):
            await interaction.response.send_message("Only staff can close tickets.", ephemeral=True)
            return

        await interaction.response.send_message("Closing ticket...", ephemeral=True)

        messages = []
        participants = set()
        async for msg in interaction.channel.history(limit=None, oldest_first=True):
            messages.append(f"[{msg.created_at}] {msg.author}: {msg.content}")
            if not msg.author.bot:
                participants.add(msg.author.mention)

        transcript_file = discord.File(
            io.StringIO("\n".join(messages)),
            filename=f"{self.ticket_type.lower()}-ticket-{self.ticket_number:04d}.txt"
        )

        config = load_config().get(str(interaction.guild.id))
        log_channel = interaction.guild.get_channel(config["log_channel_id"])

        if log_channel:
            participants_text = ", ".join(participants) if participants else "None"
            embed = discord.Embed(
                title=f"Transcript: {self.ticket_type} Ticket #{self.ticket_number}",
                description=(
                    f"**Participants:** {participants_text}\n"
                    f"**Closed by:** {interaction.user.mention}"
                ),
                color=discord.Color.blurple()
            )
            await log_channel.send(embed=embed, file=transcript_file)

        await interaction.channel.delete()

# ---------- SLASH COMMAND ----------

@tree.command(name="ticket-setup", description="Set up the ticket system")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Reply with:\n`panel_channel_id mod_role_id admin_role_id category_id log_channel_id`",
        ephemeral=True
    )

    def check(m):
        return m.author == interaction.user and m.channel == interaction.channel

    msg = await bot.wait_for("message", check=check)
    panel_id, mod_role_id, admin_role_id, category_id, log_channel_id = map(int, msg.content.split())

    config = load_config()
    config[str(interaction.guild.id)] = {
        "panel_channel_id": panel_id,
        "mod_role_id": mod_role_id,
        "admin_role_id": admin_role_id,
        "category_id": category_id,
        "log_channel_id": log_channel_id,
        "ticket_counter": 0
    }
    save_config(config)

    panel_channel = interaction.guild.get_channel(panel_id)
    embed = discord.Embed(title="🎟️ Support Tickets", description="Click below to open a ticket.", color=discord.Color.green())
    await panel_channel.send(embed=embed, view=TicketPanelView())
    await interaction.followup.send("Ticket system configured.", ephemeral=True)

# ---------- READY ----------

@bot.event
async def on_ready():
    bot.add_view(TicketPanelView())
    await tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(TOKEN)
