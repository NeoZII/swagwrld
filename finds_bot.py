import discord
from discord.ext import commands
import json, os, math
from datetime import datetime

# ═══════════════════════════════════════════════════════════
#                   ⚙️  CONFIGURATION
# ═══════════════════════════════════════════════════════════

import os
TOKEN = os.environ.get("FINDS_TOKEN")   # separate token from your main bot

GUILD_ID        = 1392191254250651688   # your server ID
FINDS_CHANNEL   = 1450479233242501151   # #your-finds channel ID
LOG_CHANNEL     = 0   # optional: channel to log point events (set 0 to disable)

# ── Points per action ────────────────────────────────────────
POINTS_POST     = 15   # base points for posting a find
POINTS_FIRE     = 5    # per 🔥 reaction received
POINTS_THUMBS   = 3    # per 👍 reaction received
POINTS_HEART    = 2    # per ❤️ reaction received
POINTS_FEATURE  = 30   # bonus for being featured by a mod (!feature)

# ── ELO Tiers ────────────────────────────────────────────────
TIERS = [
    (1000, "👑 Diamond",  0xB9F2FF),
    (600,  "💎 Platinum", 0xE5E4E2),
    (300,  "🥇 Gold",     0xFFD700),
    (100,  "🥈 Silver",   0xC0C0C0),
    (0,    "🥉 Bronze",   0xCD7F32),
]

def get_tier(points: int) -> tuple:
    for threshold, label, color in TIERS:
        if points >= threshold:
            return label, color
    return TIERS[-1][1], TIERS[-1][2]

def next_tier(points: int) -> tuple | None:
    """Returns (label, points_needed) for next tier, or None if max."""
    for threshold, label, _ in reversed(TIERS):
        if points < threshold:
            return label, threshold - points
    return None

# ═══════════════════════════════════════════════════════════

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True
intents.guilds = True     # required for on_thread_create
bot = commands.Bot(command_prefix="!f", intents=intents, help_command=None)

DATA_FILE = "finds_data.json"

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"users": {}, "posts": {}}

def save_data(d: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(d, f, indent=2)

data = load_data()

def get_user(uid: str) -> dict:
    if uid not in data["users"]:
        data["users"][uid] = {
            "points":      0,
            "weekly_pts":  0,
            "week_start":  "",
            "posts":       0,
            "reactions":   0,    # total reactions received
            "featured":    0,    # times featured by a mod
        }
    u = data["users"][uid]
    for k, v in [("weekly_pts",0),("week_start",""),("posts",0),("reactions",0),("featured",0)]:
        if k not in u: u[k] = v
    return u

def reset_weekly_if_needed(user: dict):
    week = datetime.now().strftime("%Y-W%U")
    if user.get("week_start") != week:
        user["weekly_pts"] = 0
        user["week_start"] = week

def rank_position(uid: str) -> int:
    sorted_users = sorted(data["users"].items(), key=lambda x: x[1].get("points", 0), reverse=True)
    return next((i + 1 for i, (u, _) in enumerate(sorted_users) if u == uid), 0)

def add_points(uid: str, amount: int, reason: str = "") -> tuple[int, int]:
    """Add points to user. Returns (old_points, new_points)."""
    user = get_user(uid)
    reset_weekly_if_needed(user)
    old = user["points"]
    user["points"] = max(0, old + amount)
    user["weekly_pts"] = max(0, user.get("weekly_pts", 0) + amount)
    save_data(data)
    return old, user["points"]

def tier_changed(old_pts: int, new_pts: int) -> str | None:
    old_tier = get_tier(old_pts)[0]
    new_tier = get_tier(new_pts)[0]
    if old_tier != new_tier:
        return new_tier
    return None

async def log_event(guild: discord.Guild, text: str):
    if LOG_CHANNEL:
        ch = guild.get_channel(LOG_CHANNEL)
        if ch:
            await ch.send(f"`[finds]` {text}")

# ═══════════════════════════════════════════════════════════
#                   🚪 EVENTS
# ═══════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"✅ Finds Bot live — watching #your-finds (forum mode)")

# ── Forum post detection (replaces on_message for forum channels) ──
@bot.event
async def on_thread_create(thread: discord.Thread):
    """Fires when someone creates a new post in a forum channel."""
    if thread.parent_id != FINDS_CHANNEL:
        return
    if thread.owner_id == bot.user.id:
        return

    # Fetch the starter message (same ID as the thread)
    try:
        await thread.join()  # bot needs to join thread to interact
        starter = await thread.fetch_message(thread.id)
    except Exception:
        # Fallback: wait briefly then try fetching from history
        import asyncio
        await asyncio.sleep(1)
        try:
            messages = [m async for m in thread.history(limit=1, oldest_first=True)]
            if not messages:
                return
            starter = messages[0]
        except Exception:
            return

    if starter.author.bot:
        return

    # Must contain a link or image to count
    has_link  = "http" in (starter.content or "")
    has_image = bool(starter.attachments) or bool(starter.embeds)
    if not has_link and not has_image:
        return

    uid = str(starter.author.id)
    user = get_user(uid)
    old_pts, new_pts = add_points(uid, POINTS_POST, "post")
    user["posts"] = user.get("posts", 0) + 1

    # Track this post for reaction points
    data["posts"][str(starter.id)] = {"author_id": uid, "reactions": {}}
    # Also track thread ID → starter ID mapping for reactions on thread itself
    data["posts"][str(thread.id)] = {"author_id": uid, "reactions": {}}
    save_data(data)

    tier_label, tier_color = get_tier(new_pts)
    tier_up = tier_changed(old_pts, new_pts)
    pos = rank_position(uid)

    e = discord.Embed(color=tier_color)
    e.set_author(name=f"{starter.author.display_name} posted a find!", icon_url=starter.author.display_avatar.url)
    e.description = (
        f"+**{POINTS_POST} pts** for posting\n"
        f"**Total:** {new_pts} pts  •  **Rank:** #{pos}  •  {tier_label}"
  
    )
    if tier_up:
        e.description += f"\n\n🎊 **Tier Up!** {starter.author.mention} reached **{tier_up}**!"
    e.set_footer(text="Use !flb to see the leaderboard")

    try:
        await thread.send(embed=e)
    except Exception:
        pass

    guild = bot.get_guild(thread.guild.id)
    if guild:
        await log_event(guild, f"{starter.author} posted a find (+{POINTS_POST} pts → {new_pts} total)")

@bot.event
async def on_message(message: discord.Message):
    """Only used for bot commands now — finds are tracked via on_thread_create."""
    if message.author.bot:
        return
    await bot.process_commands(message)

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return

    post_id = str(payload.message_id)
    if post_id not in data.get("posts", {}):
        return

    post = data["posts"][post_id]
    author_id = post["author_id"]

    # Don't let people boost their own posts
    if str(payload.user_id) == author_id:
        return

    emoji = str(payload.emoji)
    pts_map = {"🔥": POINTS_FIRE, "👍": POINTS_THUMBS, "❤️": POINTS_HEART}
    if emoji not in pts_map:
        return

    reactor_key = f"{payload.user_id}:{emoji}"
    if reactor_key in post.get("reactions", {}):
        return  # already counted this reaction from this user

    pts = pts_map[emoji]
    post.setdefault("reactions", {})[reactor_key] = pts

    user = get_user(author_id)
    user["reactions"] = user.get("reactions", 0) + 1
    old_pts, new_pts = add_points(author_id, pts, f"reaction {emoji}")
    tier_up = tier_changed(old_pts, new_pts)

    if tier_up:
        guild = bot.get_guild(payload.guild_id)
        if guild:
            ch = guild.get_channel(payload.channel_id)
            author = guild.get_member(int(author_id))
            if ch and author:
                e = discord.Embed(
                    description=f"🎊 {author.mention} reached **{tier_up}** on the Finds leaderboard!",
                    color=get_tier(new_pts)[1]
                )
                await ch.send(embed=e)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    post_id = str(payload.message_id)
    if post_id not in data.get("posts", {}):
        return

    post = data["posts"][post_id]
    author_id = post["author_id"]

    if str(payload.user_id) == author_id:
        return

    emoji = str(payload.emoji)
    pts_map = {"🔥": POINTS_FIRE, "👍": POINTS_THUMBS, "❤️": POINTS_HEART}
    if emoji not in pts_map:
        return

    reactor_key = f"{payload.user_id}:{emoji}"
    if reactor_key not in post.get("reactions", {}):
        return

    pts = post["reactions"].pop(reactor_key)
    add_points(author_id, -pts, f"reaction removed {emoji}")

# ═══════════════════════════════════════════════════════════
#                   🤖 COMMANDS  (prefix: !f)
# ═══════════════════════════════════════════════════════════

@bot.command(name="rank")
async def finds_rank(ctx, member: discord.Member = None):
    """Check your finds rank — !frank [@user]"""
    member = member or ctx.author
    uid = str(member.id)
    user = get_user(uid)
    pts = user["points"]
    weekly = user.get("weekly_pts", 0)
    pos = rank_position(uid)
    tier_label, tier_color = get_tier(pts)
    nt = next_tier(pts)

    desc = (
        f"**Points:** {pts:,}  •  **This week:** {weekly:,}\n"
        f"**Rank:** #{pos}  •  **Tier:** {tier_label}\n"
        f"**Finds posted:** {user.get('posts', 0)}\n"
        f"**Reactions received:** {user.get('reactions', 0)}\n"
        f"**Times featured:** {user.get('featured', 0)}\n"
    )
    if nt:
        desc += f"\n🎯 **Next tier:** {nt[0]} — {nt[1]:,} pts away"
    else:
        desc += "\n👑 **Max tier reached!** You're a Finds legend."

    e = discord.Embed(title=f"🔍 {member.display_name}'s Finds Stats", description=desc, color=tier_color)
    e.set_thumbnail(url=member.display_avatar.url)
    e.set_footer(text="Post finds in #your-finds to earn points • Reactions = bonus pts")
    await ctx.send(embed=e)

@bot.command(name="lb")
async def finds_lb(ctx, scope: str = "all"):
    """Finds leaderboard — !flb  or  !flb week"""
    weekly = scope.lower() in ("week", "weekly", "w")
    key = "weekly_pts" if weekly else "points"
    top = sorted(data["users"].items(), key=lambda x: x[1].get(key, 0), reverse=True)[:10]

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, u) in enumerate(top):
        val = u.get(key, 0)
        if val == 0:
            continue
        icon = medals[i] if i < 3 else f"`{i+1}.`"
        tier = get_tier(u.get("points", 0))[0]
        try:
            user = await bot.fetch_user(int(uid))
            name = user.display_name
        except Exception:
            name = "Unknown"
        suffix = f"{tier} • " if not weekly else ""
        lines.append(f"{icon} **{name}** — {suffix}{val:,} pts")

    title = "🏆 Weekly Finds Leaderboard" if weekly else "🏆 Finds Leaderboard"
    e = discord.Embed(title=title, description="\n".join(lines) or "No finds posted yet!", color=0x111111)
    e.set_footer(text="Post finds in #your-finds • 🔥 👍 ❤️ reactions = bonus pts for the poster")
    await ctx.send(embed=e)

@bot.command(name="top")
async def finds_top(ctx, n: int = 3):
    """Show top N finders for a giveaway — !ftop [n]  (mods only)"""
    if not ctx.author.guild_permissions.manage_guild:
        await ctx.send("❌ Mods only.", delete_after=8)
        return
    n = min(n, 10)
    top = sorted(data["users"].items(), key=lambda x: x[1].get("points", 0), reverse=True)[:n]
    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, u) in enumerate(top):
        icon = medals[i] if i < 3 else f"`{i+1}.`"
        m = ctx.guild.get_member(int(uid))
        name = m.mention if m else f"<@{uid}>"
        tier = get_tier(u.get("points", 0))[0]
        lines.append(f"{icon} {name} — **{u.get('points', 0):,} pts** • {tier}")

    e = discord.Embed(
        title=f"🏆 Top {n} Finds Contributors",
        description="\n".join(lines) or "No data yet.",
        color=0x111111
    )
    e.set_footer(text="Ready for a giveaway? Use this list to reward top contributors!")
    await ctx.send(embed=e)

@bot.command(name="eature")   # !feature (prefix is !f, so full command = !feature)
@commands.has_permissions(manage_messages=True)
async def feature(ctx, member: discord.Member = None):
    """Feature a member's find and give bonus points — !feature @user  (mods only)"""
    if not member:
        await ctx.send("❌ Usage: `!feature @user`", delete_after=8)
        return
    uid = str(member.id)
    old_pts, new_pts = add_points(uid, POINTS_FEATURE, "featured")
    user = get_user(uid)
    user["featured"] = user.get("featured", 0) + 1
    save_data(data)
    tier_label, tier_color = get_tier(new_pts)
    tier_up = tier_changed(old_pts, new_pts)

    e = discord.Embed(
        title="⭐ Find Featured!",
        description=(
            f"{member.mention}'s find was featured by {ctx.author.mention}!\n\n"
            f"+**{POINTS_FEATURE} bonus pts** awarded\n"
            f"**Total:** {new_pts:,} pts  •  {tier_label}"
            + (f"\n\n🎊 **Tier Up!** They reached **{tier_up}**!" if tier_up else "")
        ),
        color=tier_color
    )
    await ctx.send(embed=e)

@bot.command(name="reset")
@commands.has_permissions(administrator=True)
async def reset_points(ctx, member: discord.Member = None):
    """Reset a member's points — !freset @user  (admin only)"""
    if not member:
        await ctx.send("❌ Usage: `!freset @user`", delete_after=8)
        return
    uid = str(member.id)
    if uid in data["users"]:
        data["users"][uid]["points"] = 0
        data["users"][uid]["weekly_pts"] = 0
        save_data(data)
    await ctx.send(f"🔄 Reset **{member.display_name}**'s finds points to 0.")

@bot.command(name="give")
@commands.has_permissions(manage_guild=True)
async def give_points(ctx, member: discord.Member = None, amount: int = None):
    """Give finds points manually — !fgive @user [amount]  (mods only)"""
    if not member or not amount:
        await ctx.send("❌ Usage: `!fgive @user [amount]`", delete_after=8)
        return
    old_pts, new_pts = add_points(str(member.id), amount)
    tier = get_tier(new_pts)[0]
    await ctx.send(f"✅ Gave **{amount:,} pts** to {member.mention} → {new_pts:,} total  •  {tier}")

@bot.command(name="elp")   # !help (prefix is !f so full = !fhelp)
async def finds_help(ctx):
    e = discord.Embed(title="🔍 Finds Bot — Commands", color=0x111111)
    e.add_field(name="👤 Everyone", value=(
        "`!frank [@user]` — Your finds rank, points & tier\n"
        "`!flb` — All-time leaderboard\n"
        "`!flb week` — This week's top contributors\n"
        "`!fhelp` — This menu"
    ), inline=False)
    e.add_field(name="🛡️ Mods Only", value=(
        "`!ftop [n]` — Top N contributors (for giveaways)\n"
        "`!feature @user` — Feature a find (+30 bonus pts)\n"
        "`!fgive @user [amount]` — Give points manually\n"
        "`!freset @user` — Reset a user's points (admin)"
    ), inline=False)
    e.add_field(name="📈 How to earn points", value=(
        f"Post a find (link or image) in #your-finds → **+{POINTS_POST} pts**\n"
        f"Someone reacts 🔥 to your find → **+{POINTS_FIRE} pts**\n"
        f"Someone reacts 👍 to your find → **+{POINTS_THUMBS} pts**\n"
        f"Someone reacts ❤️ to your find → **+{POINTS_HEART} pts**\n"
        f"Your find gets featured by a mod → **+{POINTS_FEATURE} pts**\n\n"
        f"⚠️ You can't react to your own posts to gain points"
    ), inline=False)
    e.add_field(name="🏆 Tiers", value=(
        "🥉 Bronze: 0–99 pts\n"
        "🥈 Silver: 100–299 pts\n"
        "🥇 Gold: 300–599 pts\n"
        "💎 Platinum: 600–999 pts\n"
        "👑 Diamond: 1,000+ pts"
    ), inline=False)
    e.set_footer(text="Finds Bot • Swag WRLD")
    await ctx.send(embed=e)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission for that.", delete_after=8)
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        raise error

# ═══════════════════════════════════════════════════════════
bot.run(TOKEN)
