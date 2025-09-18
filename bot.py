import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import json
import os
import random
import subprocess

TOKEN = ""  # <-- Put your Discord bot token here
OWNER_ID = 871952588394877048  # Only this user can run /create-vps
DB_FILE = "/var/lib/vps-db.json"

SYSTEMD_IMAGES = {
    "ubuntu": "darkkop/ubuntu-systemd:22.04",
    "debian": "jrei/systemd-debian:12"
}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- Utils ---------------- #
def get_public_ip():
    try:
        return subprocess.check_output(["curl", "-s", "https://ifconfig.me"]).decode().strip()
    except:
        return "<YOUR_VPS_IP>"

async def run_cmd(*args):
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode().strip(), err.decode().strip()

def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        save_db({})
        return {}

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

async def get_status(vps_name: str):
    code, out, _ = await run_cmd("docker", "inspect", "-f", "{{.State.Status}}", vps_name)
    return out.strip() if code == 0 else "unknown"

PUBLIC_IP = get_public_ip()

# ---------------- Manage View ---------------- #
class ManageView(discord.ui.View):
    def __init__(self, vps_name: str, port: int, owner_id: int):
        super().__init__(timeout=900)
        self.vps_name = vps_name
        self.port = port
        self.owner_id = owner_id

    async def update_embed(self, interaction: discord.Interaction, msg: str = None):
        status = await get_status(self.vps_name)
        db = load_db()
        vps = db.get(self.vps_name, {})
        embed = discord.Embed(
            title=f"⚙️ VPS Manager: {self.vps_name}",
            description="Control your VPS with the buttons below:",
            color=discord.Color.blurple()
        )
        embed.add_field(name="📡 Status", value=f"`{status}`", inline=False)
        embed.add_field(name="💻 SSH", value=f"`ssh root@{PUBLIC_IP} -p {self.port}`", inline=False)
        if vps.get("password"):
            embed.add_field(name="🔑 Root Password", value=f"`{vps['password']}`", inline=False)
        embed.set_footer(text="🚀 Powered by VPS Bot")

        if msg:
            await interaction.followup.send(msg, ephemeral=True)
        try:
            await interaction.message.edit(embed=embed, view=self)
        except discord.NotFound:
            pass

    async def _docker_action(self, interaction: discord.Interaction, action: str):
        await interaction.response.defer(ephemeral=True)
        code, _, err = await run_cmd("docker", action, self.vps_name)
        if code == 0:
            await self.update_embed(interaction, f"✅ VPS `{self.vps_name}` {action}ed.")
        else:
            await interaction.followup.send(f"❌ Failed to {action}: {err}", ephemeral=True)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._docker_action(interaction, "start")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._docker_action(interaction, "stop")

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.primary)
    async def restart(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._docker_action(interaction, "restart")

    @discord.ui.button(label="❌ Delete VPS", style=discord.ButtonStyle.red)
    async def delete_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id and interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ You are not allowed.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await run_cmd("docker", "rm", "-f", self.vps_name)
        db = load_db()
        db.pop(self.vps_name, None)
        save_db(db)
        await interaction.followup.send(f"🗑️ VPS `{self.vps_name}` deleted.", ephemeral=True)
        await interaction.message.delete()

# ---------------- Commands ---------------- #
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        print(f"❌ Sync failed: {e}")

@bot.tree.command(name="create-vps", description="Create a new VPS")
async def create_vps(interaction: discord.Interaction, name: str, password: str, owner: discord.Member):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    port = random.randint(2200, 65000)

    code, _, err = await run_cmd(
        "docker", "run", "-d",
        "--name", name, "--hostname", name,
        "--privileged", "--cgroupns=host",
        "-p", f"{port}:22",
        "--memory=1g",
        "-v", "/sys/fs/cgroup:/sys/fs/cgroup:rw",
        SYSTEMD_IMAGES["ubuntu"], "/sbin/init"
    )

    if code != 0:
        await interaction.followup.send(f"❌ VPS create failed: {err}", ephemeral=True)
        return

    await run_cmd("docker", "exec", "-u", "0", name, "bash", "-lc", f"echo 'root:{password}' | chpasswd")
    await run_cmd("docker", "exec", "-u", "0", name, "bash", "-lc",
                  "apt update && apt install -y openssh-server && systemctl enable ssh && systemctl start ssh")

    db = load_db()
    db[name] = {"owner_id": owner.id, "port": port, "password": password, "name": name}
    save_db(db)

    try:
        dm = await owner.create_dm()
        embed = discord.Embed(
            title="🌐 Your VPS is Ready!",
            description="Here are your VPS details:",
            color=discord.Color.green()
        )
        embed.add_field(name="🖥️ VPS Name", value=f"`{name}`", inline=False)
        embed.add_field(name="🔑 Root Password", value=f"`{password}`", inline=False)
        embed.add_field(name="💻 SSH Login", value=f"`ssh root@{PUBLIC_IP} -p {port}`", inline=False)
        embed.set_footer(text="🚀 Powered by VPS Bot")
        await dm.send(embed=embed)
    except:
        await interaction.followup.send("⚠️ Could not DM owner.", ephemeral=True)

    await interaction.followup.send(f"✅ VPS `{name}` created for {owner.mention} (Port: {port})", ephemeral=True)

@bot.tree.command(name="manage", description="Manage your VPS")
async def manage(interaction: discord.Interaction, name: str):
    db = load_db()
    vps = db.get(name)
    if not vps:
        await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
        return
    if interaction.user.id != vps["owner_id"]:
        await interaction.response.send_message("❌ You are not the owner.", ephemeral=True)
        return

    status = await get_status(name)
    embed = discord.Embed(
        title=f"⚙️ VPS Manager: {name}",
        description="Control your VPS with the buttons below:",
        color=discord.Color.blurple()
    )
    embed.add_field(name="📡 Status", value=f"`{status}`", inline=False)
    embed.add_field(name="💻 SSH", value=f"`ssh root@{PUBLIC_IP} -p {vps['port']}`", inline=False)
    embed.add_field(name="🔑 Root Password", value=f"`{vps['password']}`", inline=False)
    embed.set_footer(text="🚀 Powered by VPS Bot")

    await interaction.response.send_message(embed=embed, view=ManageView(name, vps["port"], vps["owner_id"]), ephemeral=True)

@bot.tree.command(name="delete-vps", description="Admin: Delete a VPS")
async def delete_vps(interaction: discord.Interaction, name: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return
    db = load_db()
    if name not in db:
        await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
        return
    await run_cmd("docker", "rm", "-f", name)
    db.pop(name, None)
    save_db(db)
    await interaction.response.send_message(f"🗑️ VPS `{name}` deleted.", ephemeral=True)

@bot.tree.command(name="list", description="List your VPS")
async def list_vps(interaction: discord.Interaction):
    db = load_db()
    user_vps = [n for n, v in db.items() if v["owner_id"] == interaction.user.id]
    if not user_vps:
        await interaction.response.send_message("📭 You have no VPS.", ephemeral=True)
        return
    embed = discord.Embed(title="📋 Your VPS List", color=discord.Color.green())
    embed.add_field(name="Servers", value="\n".join(f"`{n}`" for n in user_vps), inline=False)
    embed.set_footer(text="🚀 Powered by VPS Bot")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! `{round(bot.latency*1000)}ms`", ephemeral=True)

bot.run(TOKEN)