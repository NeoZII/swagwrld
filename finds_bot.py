import discord
from discord.ext import commands
import os, math
from datetime import datetime
from pymongo import MongoClient

# ═══════════════════════════════════════════════════════════
#                   ⚙️  CONFIGURATION
# ═══════════════════════════════════════════════════════════

TOKEN     = os.environ.get("FINDS_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")

GUILD_ID        = 1392191254250651688
FINDS_CHANNEL   = 1450479233242501151
LOG_CHANNEL     = 0

POINTS_POST    = 15
POINTS_FIRE    = 5
POINTS_THUMBS  = 3
POINTS_HEART   = 2
POINTS_FEATURE = 30

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

def next_tier(points: int):
    for threshold, label, _ in reversed(TIERS):
        if points < threshold:
            return label, threshold - points
    return None

def tier_changed(old_pts: int, new_pts: int):
    old = get_tier(old_pts)[0]
    new = get_tier(new_pts)[0]
    return new if old != new else None

# ═══════════════════════════════════════════════════════════
#                   🗄️  MONGODB SETUP
# ═══════════════════════════════════════════════════════════

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True
intents.guilds = True
bot = commands.Bot(command_prefix="!f", intents=intents, help_command=None)

mongo      = MongoClient(MONGO_URI)
db         = mongo["swagwrld"]
finds_col  = db["finds_users"]
posts_col  = db["finds_posts"]

def get_user(uid: str) -> dict:
    user = finds_col.find_one({"_id": uid})
    if not user:
        user = {"_id": uid, "points": 0, "weekly_pts": 0, "week_start": "", "posts": 0, "reactions": 0, "featured": 0}
        finds_col.insert_one(user)
    for k, v in [("weekly_pts",0),("week_start",""),("posts",0),("reactions",0),("featured",0)]:
        if k not in user: user[k] = v
    return user

def save_user(user: dict):
    finds_col.replace_one({"_id": user["_id"]}, user, upsert=True)

def get_post(msg_id: str):
    return posts_col.find_one({"_id": msg_id})

def save_post(msg_id: str, author_id: str):
    posts_col.replace_one({"_id": msg_id}, {"_id": msg_id, "author_id": author_id, "reactions": {}}, upsert=True)

def reset_weekly_if_needed(user: dict):
    week = datetime.now().strftime("%Y-W%U")
    if user.get("week_start") != week:
        user["weekly_xp"] = 0
        user["week_start"] = week

def add_points(uid: str, amount: int) -> tuple:
    user = get_user(uid)
    reset_weekly_if_needed(user)
    old = user["points"]
    user["points"] = max(0, old + amount)
    user["weekly_pts"] = max(0, user.get("weekly_pts", 0) + amount)
    save_user(user)
    return old, user["points"]

def rank_position(uid: str) -> int:
    user = get_user(uid)
    return finds_col.count_documents({"points": {"$gt": user["points"]}}) + 1

async def log_event(guild: discord.Guild, text: str):
    if LOG_CHANNEL:
        ch = guild.get_channel(LOG_CHANNEL)
        if ch: await ch.send(f"`[finds]` {text}")

# ═══════════════════════════════════════════════════════════
#                   🚪 EVENTS
# ═══════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"✅ Finds Bot live — watching #your-finds (forum mode)")

@bot.event
async def on_thread_create(thread: discord.Thread):
    if thread.parent_id != FINDS_CHANNEL: return
    if thread.owner_id == bot.user.id: return

    try:
        await thread.join()
        starter = await thread.fetch_message(thread.id)
    except:
        import asyncio
        await asyncio.sleep(1)
        try:
            messages = [m async for m in thread.history(limit=1, oldest_first=True)]
            if not messages: return
            starter = messages[0]
        except: return

    if starter.author.bot: return
    has_link  = "http" in (starter.content or "")
    has_image = bool(starter.attachments) or bool(starter.embeds)
    if not has_link and not has_image: return

    uid = str(starter.author.id)
    user = get_user(uid)
    old_pts, new_pts = add_points(uid, POINTS_POST)
    user["posts"] = user.get("posts", 0) + 1
    save_user(user)

    save_post(str(starter.id), uid)
    save_post(str(thread.id), uid)

    tier_label, tier_color = get_tier(new_pts)
    tier_up = tier_changed(old_pts, new_pts)
    pos = rank_position(uid)

    e = discord.Embed(color=tier_color)
    e.set_author(name=f"{starter.author.display_name} posted a find!", icon_url=starter.author.display_avatar.url)
    e.description = (f"+**{POINTS_POST} pts** for posting\n"
        f"**Total:** {new_pts} pts  •  **Rank:** #{pos}  •  {tier_label}")
    if tier_up: e.description += f"\n\n🎊 **Tier Up!** {starter.author.mention} reached **{tier_up}**!"
    e.set_footer(text="Use !flb to see the leaderboard")

    try: await thread.send(embed=e)
    except: pass

    guild = bot.get_guild(thread.guild.id)
    if guild: await log_event(guild, f"{starter.author} posted a find (+{POINTS_POST} pts → {new_pts} total)")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return
    await bot.process_commands(message)

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id: return
    post = get_post(str(payload.message_id))
    if not post: return
    author_id = post["author_id"]
    if str(payload.user_id) == author_id: return

    emoji = str(payload.emoji)
    pts_map = {"🔥": POINTS_FIRE, "👍": POINTS_THUMBS, "❤️": POINTS_HEART}
    if emoji not in pts_map: return

    reactor_key = f"{payload.user_id}:{emoji}"
    if reactor_key in post.get("reactions", {}): return

    pts = pts_map[emoji]
    post.setdefault("reactions", {})[reactor_key] = pts
    posts_col.replace_one({"_id": post["_id"]}, post, upsert=True)

    user = get_user(author_id)
    user["reactions"] = user.get("reactions", 0) + 1
    save_user(user)
    old_pts, new_pts = add_points(author_id, pts)
    tier_up = tier_changed(old_pts, new_pts)

    if tier_up:
        guild = bot.get_guild(payload.guild_id)
        if guild:
            ch = guild.get_channel(payload.channel_id)
            author = guild.get_member(int(author_id))
            if ch and author:
                e = discord.Embed(description=f"🎊 {author.mention} reached **{tier_up}** on the Finds leaderboard!", color=get_tier(new_pts)[1])
                await ch.send(embed=e)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    post = get_post(str(payload.message_id))
    if not post: return
    author_id = post["author_id"]
    if str(payload.user_id) == author_id: return

    emoji = str(payload.emoji)
    pts_map = {"🔥": POINTS_FIRE, "👍": POINTS_THUMBS, "❤️": POINTS_HEART}
    if emoji not in pts_map: return

    reactor_key = f"{payload.user_id}:{emoji}"
    reactions = post.get("reactions", {})
    if reactor_key not in reactions: return

    pts = reactions.pop(reactor_key)
    posts_col.replace_one({"_id": post["_id"]}, post, upsert=True)
    add_points(author_id, -pts)

# ═══════════════════════════════════════════════════════════
#                   🤖 COMMANDS
# ═══════════════════════════════════════════════════════════

@bot.command(name="rank")
async def finds_rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    uid = str(member.id)
    user = get_user(uid)
    pts = user["points"]
    weekly = user.get("weekly_pts", 0)
    pos = rank_position(uid)
    tier_label, tier_color = get_tier(pts)
    nt = next_tier(pts)

    desc = (f"**Points:** {pts:,}  •  **This week:** {weekly:,}\n"
        f"**Rank:** #{pos}  •  **Tier:** {tier_label}\n"
        f"**Finds posted:** {user.get('posts',0)}\n"
        f"**Reactions received:** {user.get('reactions',0)}\n"
        f"**Times featured:** {user.get('featured',0)}\n")
    if nt: desc += f"\n🎯 **Next tier:** {nt[0]} — {nt[1]:,} pts away"
    else:   desc += "\n👑 **Max tier reached!** You're a Finds legend."

    e = discord.Embed(title=f"🔍 {member.display_name}'s Finds Stats", description=desc, color=tier_color)
    e.set_thumbnail(url=member.display_avatar.url)
    e.set_footer(text="Post finds in #your-finds to earn points • Reactions = bonus pts")
    await ctx.send(embed=e)

@bot.command(name="lb")
async def finds_lb(ctx, scope: str = "all"):
    weekly = scope.lower() in ("week","weekly","w")
    key = "weekly_pts" if weekly else "points"
    top = list(finds_col.find().sort(key, -1).limit(10))
    medals = ["🥇","🥈","🥉"]
    lines = []
    for i, u in enumerate(top):
        val = u.get(key, 0)
        if val == 0: continue
        icon = medals[i] if i < 3 else f"`{i+1}.`"
        tier = get_tier(u.get("points",0))[0]
        try: usr = await bot.fetch_user(int(u["_id"])); name = usr.display_name
        except: name = "Unknown"
        suffix = f"{tier} • " if not weekly else ""
        lines.append(f"{icon} **{name}** — {suffix}{val:,} pts")
    title = "🏆 Weekly Finds Leaderboard" if weekly else "🏆 Finds Leaderboard"
    e = discord.Embed(title=title, description="\n".join(lines) or "No finds posted yet!", color=0x111111)
    e.set_footer(text="Post finds in #your-finds • 🔥 👍 ❤️ reactions = bonus pts")
    await ctx.send(embed=e)

@bot.command(name="top")
async def finds_top(ctx, n: int = 3):
    if not ctx.author.guild_permissions.manage_guild:
        await ctx.send("❌ Mods only.", delete_after=8); return
    n = min(n, 10)
    top = list(finds_col.find().sort("points", -1).limit(n))
    medals = ["🥇","🥈","🥉"]
    lines = []
    for i, u in enumerate(top):
        icon = medals[i] if i < 3 else f"`{i+1}.`"
        m = ctx.guild.get_member(int(u["_id"]))
        name = m.mention if m else f"<@{u['_id']}>"
        tier = get_tier(u.get("points",0))[0]
        lines.append(f"{icon} {name} — **{u.get('points',0):,} pts** • {tier}")
    e = discord.Embed(title=f"🏆 Top {n} Finds Contributors", description="\n".join(lines) or "No data yet.", color=0x111111)
    e.set_footer(text="Ready for a giveaway? Use this list to reward top contributors!")
    await ctx.send(embed=e)

@bot.command(name="eature")
@commands.has_permissions(manage_messages=True)
async def feature(ctx, member: discord.Member = None):
    if not member:
        await ctx.send("❌ Usage: `!feature @user`", delete_after=8); return
    uid = str(member.id)
    old_pts, new_pts = add_points(uid, POINTS_FEATURE)
    user = get_user(uid)
    user["featured"] = user.get("featured", 0) + 1
    save_user(user)
    tier_label, tier_color = get_tier(new_pts)
    tier_up = tier_changed(old_pts, new_pts)
    e = discord.Embed(title="⭐ Find Featured!",
        description=(f"{member.mention}'s find was featured by {ctx.author.mention}!\n\n"
            f"+**{POINTS_FEATURE} bonus pts** awarded\n**Total:** {new_pts:,} pts  •  {tier_label}"
            + (f"\n\n🎊 **Tier Up!** They reached **{tier_up}**!" if tier_up else "")),
        color=tier_color)
    await ctx.send(embed=e)

@bot.command(name="give")
@commands.has_permissions(manage_guild=True)
async def give_points(ctx, member: discord.Member = None, amount: int = None):
    if not member or not amount:
        await ctx.send("❌ Usage: `!fgive @user [amount]`", delete_after=8); return
    old_pts, new_pts = add_points(str(member.id), amount)
    tier = get_tier(new_pts)[0]
    await ctx.send(f"✅ Gave **{amount:,} pts** to {member.mention} → {new_pts:,} total  •  {tier}")

@bot.command(name="reset")
@commands.has_permissions(administrator=True)
async def reset_points(ctx, member: discord.Member = None):
    if not member:
        await ctx.send("❌ Usage: `!freset @user`", delete_after=8); return
    finds_col.update_one({"_id": str(member.id)}, {"$set": {"points": 0, "weekly_pts": 0}})
    await ctx.send(f"🔄 Reset **{member.display_name}**'s finds points to 0.")

@bot.command(name="elp")
async def finds_help(ctx):
    e = discord.Embed(title="🔍 Finds Bot — Commands", color=0x111111)
    e.add_field(name="👤 Everyone", value=(
        "`!frank [@user]` — Finds rank, points & tier\n`!flb` — All-time leaderboard\n"
        "`!flb week` — This week's top contributors\n`!fhelp` — This menu"), inline=False)
    e.add_field(name="🛡️ Mods Only", value=(
        "`!ftop [n]` — Top N contributors (for giveaways)\n`!feature @user` — Feature a find (+30 bonus pts)\n"
        "`!fgive @user [amount]` — Give points manually\n`!freset @user` — Reset a user's points (admin)"), inline=False)
    e.add_field(name="📈 How to earn points", value=(
        f"Post a find (link or image) → **+{POINTS_POST} pts**\n"
        f"Someone reacts 🔥 → **+{POINTS_FIRE} pts**\n"
        f"Someone reacts 👍 → **+{POINTS_THUMBS} pts**\n"
        f"Someone reacts ❤️ → **+{POINTS_HEART} pts**\n"
        f"Find featured by mod → **+{POINTS_FEATURE} pts**\n⚠️ You can't react to your own posts"), inline=False)
    e.add_field(name="🏆 Tiers", value="🥉 Bronze: 0–99 pts\n🥈 Silver: 100–299 pts\n🥇 Gold: 300–599 pts\n💎 Platinum: 600–999 pts\n👑 Diamond: 1,000+ pts", inline=False)
    e.set_footer(text="Finds Bot • Swag WRLD")
    await ctx.send(embed=e)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission for that.", delete_after=8)
    elif isinstance(error, commands.CommandNotFound): pass
    else: raise error

bot.run(TOKEN)
