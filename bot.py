import discord
from discord.ext import commands, tasks
import os, random, asyncio, math
from datetime import datetime, timedelta
from pymongo import MongoClient

# ═══════════════════════════════════════════════════════════
#                   ⚙️  CONFIGURATION
# ═══════════════════════════════════════════════════════════

TOKEN     = os.environ.get("TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")

GUILD_ID         = 0
WELCOME_CHANNEL  = 0
GENERAL_CHANNEL  = 0
VERIFY_CHANNEL   = 0
LEVELUP_CHANNEL  = 0
FORUM_CHANNELS   = set()

ROLE_NEW         = 0
ROLE_KAKOBUY     = 0
ROLE_REGULAR     = 0
ROLE_OG          = 0
ROLE_VIP         = 0

GIVEAWAY_MIN_MESSAGES = 20
GIVEAWAY_NEEDS_KAKOBUY = True

XP_MIN         = 15
XP_MAX         = 25
XP_COOLDOWN    = 60
XP_DAILY_BONUS = 100

KAKOBUY_LINK = "https://ikako.vip/r/dqh2u"

# ═══════════════════════════════════════════════════════════
#                   🗄️  MONGODB SETUP
# ═══════════════════════════════════════════════════════════

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

mongo         = MongoClient(MONGO_URI)
db            = mongo["swagwrld"]
users_col     = db["users"]
settings_col  = db["settings"]
giveaways_col = db["giveaways"]

def get_user(uid: str) -> dict:
    user = users_col.find_one({"_id": uid})
    if not user:
        user = {
            "_id": uid, "xp": 0, "messages": 0, "last_xp": 0,
            "last_daily": "", "weekly_xp": 0, "week_start": "",
            "checkin_streak": 0, "last_checkin": "", "best_streak": 0,
        }
        users_col.insert_one(user)
    for k, v in [("checkin_streak",0),("last_checkin",""),("best_streak",0),("weekly_xp",0),("week_start","")]:
        if k not in user: user[k] = v
    return user

def save_user(user: dict):
    users_col.replace_one({"_id": user["_id"]}, user, upsert=True)

def get_setting(key: str, default=None):
    doc = settings_col.find_one({"_id": key})
    return doc["value"] if doc else default

def set_setting(key: str, value):
    settings_col.replace_one({"_id": key}, {"_id": key, "value": value}, upsert=True)

def save_giveaway(msg_id: str, prize: str, ends: float, channel_id: int):
    giveaways_col.replace_one({"_id": msg_id}, {"_id": msg_id, "prize": prize, "ends": ends, "channel_id": channel_id}, upsert=True)

def delete_giveaway(msg_id: str):
    giveaways_col.delete_one({"_id": msg_id})

def get_active_giveaway(msg_id: str):
    return giveaways_col.find_one({"_id": msg_id})

def is_boost_active() -> bool:
    ends = get_setting("boost_ends", 0)
    return datetime.now().timestamp() < ends

def reset_weekly_if_needed(user: dict):
    week = datetime.now().strftime("%Y-W%U")
    if user.get("week_start") != week:
        user["weekly_xp"] = 0
        user["week_start"] = week

# ═══════════════════════════════════════════════════════════
#                   📊 LEVEL / XP ENGINE
# ═══════════════════════════════════════════════════════════

def xp_for_next_level(level: int) -> int:
    return 5 * (level ** 2) + 50 * level + 100

def compute_level(total_xp: int):
    level, xp = 0, total_xp
    while xp >= xp_for_next_level(level):
        xp -= xp_for_next_level(level)
        level += 1
    return level, xp, xp_for_next_level(level)

def progress_bar(current: int, total: int, length: int = 12) -> str:
    filled = math.floor((current / total) * length) if total else 0
    return "█" * filled + "░" * (length - filled)

LEVEL_MILESTONES = {
    5:  (ROLE_REGULAR, "💬 Regular"),
    10: (ROLE_OG,      "🔥 OG"),
    20: (ROLE_VIP,     "👑 VIP"),
}
LEVEL_ROLE_IDS = {ROLE_NEW, ROLE_REGULAR, ROLE_OG, ROLE_VIP}

async def apply_level_roles(member: discord.Member, level: int) -> list:
    guild = member.guild
    best_id, best_label = ROLE_NEW, "🆕 New Member"
    for lvl, (rid, label) in sorted(LEVEL_MILESTONES.items()):
        if level >= lvl:
            best_id, best_label = rid, label
    current = [r for r in member.roles if r.id in LEVEL_ROLE_IDS]
    if any(r.id == best_id for r in current):
        return []
    to_remove = [r for r in current if r.id != best_id and r.id != ROLE_NEW]
    if to_remove:
        await member.remove_roles(*to_remove)
    new_role = guild.get_role(best_id)
    if new_role:
        await member.add_roles(new_role)
        if best_id != ROLE_NEW:
            return [best_label]
    return []

# ═══════════════════════════════════════════════════════════
#                   📅 DAILY POSTS
# ═══════════════════════════════════════════════════════════

DAILY_QUESTIONS = [
    "What's the best piece you've ever copped? Drop a pic 📸",
    "What item are you hunting for right now? 🔍",
    "What's your go-to brand on Kakobuy right now? Drop names 👇",
    "What's one piece you regret NOT buying?",
    "Streetwear or luxury — what's your vibe today?",
    "Drop your grail item 👇 let's see what the community is chasing",
    "Best haul you've received this month? Post it in #hauls 📦",
    "Favourite tip you've learned about using agents?",
    "What's a brand you think is underrated on Kakobuy?",
    "Rate your current rotation out of 10 — what's the weakest piece?",
    "If you could only buy from one brand for a year, what would it be?",
    "What's your biggest W cop ever? Let's hear it 🏆",
    "Best budget find under $30 on Kakobuy? Show us 🔥",
    "What's your most worn piece right now?",
    "Archive fashion or hype brands — which side are you on?",
]

daily_q_index = [0]
DAILY_SCHEDULE = {0:"question",1:"finds",2:"question",3:"fit_pic",4:"question",5:"deals",6:"recap"}

@tasks.loop(hours=24)
async def daily_post():
    guild = bot.get_guild(GUILD_ID)
    if not guild: return
    ch = guild.get_channel(GENERAL_CHANNEL)
    if not ch: return
    day = datetime.now().weekday()
    ptype = DAILY_SCHEDULE.get(day, "question")

    if ptype == "question":
        q = DAILY_QUESTIONS[daily_q_index[0] % len(DAILY_QUESTIONS)]
        daily_q_index[0] += 1
        e = discord.Embed(title="💬 Daily Question", description=q, color=0x111111)
        e.set_footer(text="Swag WRLD • Chatting earns XP — drop your answer 👇")
        await ch.send(embed=e)
    elif ptype == "finds":
        e = discord.Embed(title="📬 Share Your Finds — Tuesday",
            description=f"Found something 🔥 on Kakobuy? Drop it in #your-finds!\n\nNot registered? → {KAKOBUY_LINK}\nThen `!verify [username]` to unlock giveaway access 🔓", color=0x111111)
        await ch.send(embed=e)
    elif ptype == "fit_pic":
        e = discord.Embed(title="📸 Fit Pic Thursday",
            description="Show the server what you've been rocking this week!\nDrop it in #fit-pics — best fit gets a shoutout 🤙\n\n💡 Every message = XP. Keep chatting to level up!", color=0x111111)
        await ch.send(embed=e)
    elif ptype == "deals":
        e = discord.Embed(title="💰 Weekend Finds",
            description=f"Check #your-finds for the hottest items this weekend!\n\nGrab your **$300 Kakobuy bonus** → {KAKOBUY_LINK}\nThen `!verify [username]` to unlock full access 🔓", color=0x111111)
        await ch.send(embed=e)
    elif ptype == "recap":
        top = list(users_col.find().sort("weekly_xp", -1).limit(5))
        medals = ["🥇","🥈","🥉","`4.`","`5.`"]
        lines = []
        for i, u in enumerate(top):
            wxp = u.get("weekly_xp", 0)
            if wxp == 0: continue
            try:
                usr = await bot.fetch_user(int(u["_id"]))
                name = usr.display_name
            except: name = "Unknown"
            lines.append(f"{medals[i]} **{name}** — {wxp:,} XP this week")
        desc = "**Most active members this week:**\n\n"
        desc += "\n".join(lines) if lines else "No activity tracked yet!"
        desc += "\n\nDrop your best cop of the week in #hauls 🏆"
        e = discord.Embed(title="🔁 Weekly Recap", description=desc, color=0x111111)
        e.set_footer(text="Swag WRLD • New week starts tomorrow!")
        await ch.send(embed=e)

# ═══════════════════════════════════════════════════════════
#                   🚪 EVENTS
# ═══════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"✅ {bot.user} is live — Swag WRLD bot ready!")
    daily_post.start()

@bot.event
async def on_member_join(member: discord.Member):
    role = member.guild.get_role(ROLE_NEW)
    if role:
        await member.add_roles(role)
    ch = member.guild.get_channel(WELCOME_CHANNEL)
    if not ch: return
    e = discord.Embed(
        title=f"Welcome to Swag WRLD, {member.display_name}! 🖤",
        description=(
            f"You just joined **{member.guild.member_count}** fashion enthusiasts 🔥\n\n"
            "**Get started:**\n📋 Read #rules\n🛒 Learn how to order in #agent-tutorial\n💬 Introduce yourself in #general\n\n"
            "**🔑 Unlock giveaways:**\n"
            f"1. Register on Kakobuy → {KAKOBUY_LINK}\n"
            "2. Type `!verify [kakobuy username]`\n"
            "3. Get approved → **🛒 Kakobuy Member** role\n"
            f"4. Send at least **{GIVEAWAY_MIN_MESSAGES} messages** in the server\n\n"
            "**📈 Level system:**\nEvery message earns XP (60s cooldown)\n"
            "First message each day = **+100 bonus XP** ☀️\n"
            "Level 5 → 💬 Regular  •  Level 10 → 🔥 OG  •  Level 20 → 👑 VIP\n\n"
            "Use `!rank` anytime to see your progress!"
        ), color=0x111111)
    e.set_thumbnail(url=member.display_avatar.url)
    e.set_footer(text=f"Member #{member.guild.member_count} • Swag WRLD")
    await ch.send(embed=e)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild: return
    if isinstance(message.channel, discord.Thread):
        parent = message.channel.parent
        if parent and parent.id in FORUM_CHANNELS:
            await bot.process_commands(message)
            return

    uid = str(message.author.id)
    user = get_user(uid)
    user["messages"] += 1
    reset_weekly_if_needed(user)
    now = datetime.now().timestamp()

    if now - user.get("last_xp", 0) >= XP_COOLDOWN:
        xp_gained = random.randint(XP_MIN, XP_MAX)
        if is_boost_active(): xp_gained *= 2
        today = datetime.now().strftime("%Y-%m-%d")
        if user.get("last_daily") != today:
            xp_gained += XP_DAILY_BONUS
            user["last_daily"] = today
            try:
                await message.author.send(
                    f"☀️ **Daily bonus!** +{XP_DAILY_BONUS} XP for your first message today!\n"
                    f"Keep chatting on Swag WRLD to level up 📈  |  `!rank` to check progress"
                )
            except: pass

        old_level = compute_level(user["xp"])[0]
        user["xp"] += xp_gained
        user["weekly_xp"] = user.get("weekly_xp", 0) + xp_gained
        user["last_xp"] = now
        new_level, xp_into, xp_needed = compute_level(user["xp"])

        if new_level > old_level:
            earned = await apply_level_roles(message.author, new_level)
            alert_ch = message.guild.get_channel(LEVELUP_CHANNEL) if LEVELUP_CHANNEL else message.channel
            bar = progress_bar(xp_into, xp_needed)
            role_txt = f"\n🎊 You unlocked **{earned[0]}**!" if earned else ""
            e = discord.Embed(title="⬆️ Level Up!",
                description=(f"{message.author.mention} reached **Level {new_level}**!{role_txt}\n\n"
                    f"`{bar}` {xp_into:,} / {xp_needed:,} XP\nUse `!rank` to see your full stats."), color=0x111111)
            e.set_thumbnail(url=message.author.display_avatar.url)
            if alert_ch: await alert_ch.send(embed=e)

    save_user(user)
    await bot.process_commands(message)

# ═══════════════════════════════════════════════════════════
#                   🤖 COMMANDS
# ═══════════════════════════════════════════════════════════

CHECKIN_MILESTONES = {7:"🔥",14:"💎",30:"👑",50:"🌟",100:"🏆"}

def checkin_xp_reward(streak: int) -> int:
    if streak >= 30: return 300
    if streak >= 14: return 200
    if streak >= 7:  return 150
    return 75

@bot.command(name="checkin", aliases=["ci"])
async def checkin(ctx):
    uid = str(ctx.author.id)
    user = get_user(uid)
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    last = user.get("last_checkin", "")

    if last == today:
        streak = user["checkin_streak"]
        next_ms = next((m for m in sorted(CHECKIN_MILESTONES) if m > streak), None)
        e = discord.Embed(title="⏰ Already Checked In",
            description=(f"You already checked in today!\n\n**Current streak:** {streak} day{'s' if streak!=1 else ''} 🔥\n"
                f"{'**Next milestone:** '+str(next_ms)+' days → '+CHECKIN_MILESTONES[next_ms] if next_ms else '**Max milestone reached!** 🏆'}\n\nCome back tomorrow!"),
            color=0x111111)
        await ctx.send(embed=e, delete_after=15)
        return

    if last == yesterday:
        user["checkin_streak"] += 1
    elif last == "":
        user["checkin_streak"] = 1
    else:
        broken = user["checkin_streak"]
        user["checkin_streak"] = 1
        if broken > 1:
            try:
                await ctx.author.send(f"😔 Your **{broken}-day streak** was broken. Starting fresh — don't miss tomorrow!")
            except: pass

    user["last_checkin"] = today
    streak = user["checkin_streak"]
    if streak > user.get("best_streak", 0): user["best_streak"] = streak

    reward = checkin_xp_reward(streak)
    if is_boost_active(): reward *= 2
    old_level = compute_level(user["xp"])[0]
    user["xp"] += reward
    reset_weekly_if_needed(user)
    user["weekly_xp"] = user.get("weekly_xp", 0) + reward
    new_level = compute_level(user["xp"])[0]
    save_user(user)

    if new_level > old_level:
        await apply_level_roles(ctx.author, new_level)

    milestone_hit = CHECKIN_MILESTONES.get(streak)
    next_ms = next((m for m in sorted(CHECKIN_MILESTONES) if m > streak), None)
    streak_bar = "🔥" * min(streak, 10) + ("+" if streak > 10 else "")
    desc = (f"**Streak:** {streak} day{'s' if streak!=1 else ''} {streak_bar}\n"
        f"**Best ever:** {user['best_streak']} days\n"
        f"**XP earned:** +{reward:,} XP{'  ⚡ 2x boost!' if is_boost_active() else ''}\n"
        f"**Total XP:** {user['xp']:,}\n\n")
    if milestone_hit: desc += f"🎊 **MILESTONE!** {streak} day streak — {milestone_hit} You're on fire!\n\n"
    if next_ms:
        desc += f"🎯 **Next milestone:** {next_ms} days ({next_ms-streak} day{'s' if next_ms-streak!=1 else ''} away) → {CHECKIN_MILESTONES[next_ms]}"
    else:
        desc += "👑 **Max milestone reached!** You're a Swag WRLD legend."
    desc += "\n\n*Come back tomorrow to keep the streak going!*"

    title = (f"{'🎊 MILESTONE! ' if milestone_hit else ''}✅ Check-in #{streak}")
    e = discord.Embed(title=title, description=desc, color=0x111111)
    e.set_thumbnail(url=ctx.author.display_avatar.url)
    e.set_footer(text="Swag WRLD • Daily check-in")
    await ctx.send(embed=e)

@bot.command(name="rank")
async def rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    uid = str(member.id)
    user = get_user(uid)
    level, xp_into, xp_needed = compute_level(user["xp"])
    bar = progress_bar(xp_into, xp_needed)
    msgs = user.get("messages", 0)
    weekly = user.get("weekly_xp", 0)
    streak = user.get("checkin_streak", 0)
    best   = user.get("best_streak", 0)

    role_label = "🆕 New Member"
    for lvl, (_, label) in sorted(LEVEL_MILESTONES.items(), reverse=True):
        if level >= lvl: role_label = label; break

    next_milestone = None
    for lvl, (_, label) in sorted(LEVEL_MILESTONES.items()):
        if level < lvl: next_milestone = (lvl, label); break

    rank_pos = users_col.count_documents({"xp": {"$gt": user["xp"]}}) + 1

    kakobuy_role = ctx.guild.get_role(ROLE_KAKOBUY)
    is_verified  = bool(kakobuy_role and kakobuy_role in member.roles)
    has_msgs     = msgs >= GIVEAWAY_MIN_MESSAGES
    gw_eligible  = is_verified and has_msgs

    desc = (f"**Level:** {level}  •  **Server Rank:** #{rank_pos}\n"
        f"**Total XP:** {user['xp']:,}  •  **This week:** {weekly:,}\n"
        f"**Messages sent:** {msgs:,}\n**Role:** {role_label}\n"
        f"**Check-in streak:** {streak} day{'s' if streak!=1 else ''} 🔥  •  Best: {best} days\n\n"
        f"**Progress → Level {level+1}**\n`{bar}` {xp_into:,} / {xp_needed:,} XP\n")
    if next_milestone:
        desc += f"\n🎯 **Next role:** {next_milestone[1]} at Level {next_milestone[0]} ({next_milestone[0]-level} level(s) away)"
    desc += f"\n\n**🎉 Giveaway eligible:** {'✅ Yes' if gw_eligible else '❌ No'}"
    if not gw_eligible:
        if not is_verified: desc += f"\n→ Need **🛒 Kakobuy Member** role — `!verify [username]`"
        if not has_msgs:    desc += f"\n→ Need **{GIVEAWAY_MIN_MESSAGES-msgs} more messages**"

    e = discord.Embed(title=f"📊 {member.display_name}'s Stats", description=desc, color=0x111111)
    e.set_thumbnail(url=member.display_avatar.url)
    e.set_footer(text=f"{'⚡ 2x XP Active!' if is_boost_active() else 'Swag WRLD'}")
    await ctx.send(embed=e)

@bot.command(name="leaderboard", aliases=["lb","top"])
async def leaderboard(ctx, scope: str = "all"):
    weekly = scope.lower() in ("week","weekly","w")
    key = "weekly_xp" if weekly else "xp"
    top = list(users_col.find().sort(key, -1).limit(10))
    medals = ["🥇","🥈","🥉"]
    lines = []
    for i, u in enumerate(top):
        val = u.get(key, 0)
        if val == 0: continue
        icon = medals[i] if i < 3 else f"`{i+1}.`"
        lvl = compute_level(u.get("xp",0))[0]
        try: usr = await bot.fetch_user(int(u["_id"])); name = usr.display_name
        except: name = "Unknown"
        suffix = f"Lvl {lvl} • " if not weekly else ""
        lines.append(f"{icon} **{name}** — {suffix}{val:,} XP")
    title = "🏆 Weekly Leaderboard" if weekly else "🏆 All-Time Leaderboard"
    e = discord.Embed(title=title, description="\n".join(lines) or "No data yet!", color=0x111111)
    e.set_footer(text="Use !leaderboard week for this week's top • Keep chatting to climb!")
    await ctx.send(embed=e)

@bot.command(name="verify")
async def verify(ctx, kakobuy_username: str = None):
    if not kakobuy_username:
        await ctx.send(f"❌ Usage: `!verify [your kakobuy username]`\n\nNo account? → {KAKOBUY_LINK}", delete_after=15)
        return
    kakobuy_role = ctx.guild.get_role(ROLE_KAKOBUY)
    if kakobuy_role and kakobuy_role in ctx.author.roles:
        await ctx.send("✅ You're already a **🛒 Kakobuy Member**!", delete_after=8)
        return
    verify_ch = ctx.guild.get_channel(VERIFY_CHANNEL)
    if not verify_ch:
        await ctx.send("❌ Verify channel not configured. Contact a mod!", delete_after=10)
        return
    uid = str(ctx.author.id)
    user = get_user(uid)
    msgs = user.get("messages", 0)
    level = compute_level(user["xp"])[0]
    e = discord.Embed(title="🔐 Verification Request",
        description=(f"**User:** {ctx.author.mention} (`{ctx.author}`)\n"
            f"**Kakobuy Username:** `{kakobuy_username}`\n**Messages sent:** {msgs}\n**Level:** {level}\n\n"
            "React ✅ to approve → gives **🛒 Kakobuy Member** role\nReact ❌ to deny"),
        color=0x111111)
    e.set_thumbnail(url=ctx.author.display_avatar.url)
    e.set_footer(text=f"User ID: {ctx.author.id}")
    req = await verify_ch.send(embed=e)
    await req.add_reaction("✅")
    await req.add_reaction("❌")
    msgs_needed = max(0, GIVEAWAY_MIN_MESSAGES - msgs)
    await ctx.send(
        f"📨 **Verification submitted!** A mod will review shortly.\n"
        f"{'⚠️ You also need **'+str(msgs_needed)+' more messages** to enter giveaways.' if msgs_needed > 0 else '✅ You already meet the message requirement!'}",
        delete_after=20)
    try: await ctx.message.delete()
    except: pass

@bot.command(name="approve")
@commands.has_permissions(manage_roles=True)
async def approve(ctx, member: discord.Member = None):
    if not member:
        await ctx.send("❌ Usage: `!approve @user`", delete_after=8); return
    role = ctx.guild.get_role(ROLE_KAKOBUY)
    if not role:
        await ctx.send("❌ ROLE_KAKOBUY not configured.", delete_after=8); return
    if role in member.roles:
        await ctx.send(f"ℹ️ {member.mention} is already verified.", delete_after=8); return
    await member.add_roles(role)
    msgs = get_user(str(member.id)).get("messages", 0)
    await ctx.send(f"✅ {member.mention} approved → **🛒 Kakobuy Member** given! (Messages: {msgs}/{GIVEAWAY_MIN_MESSAGES})")
    try:
        await member.send(
            f"✅ **Verified on Swag WRLD!** You have the **🛒 Kakobuy Member** role 🔥\n"
            f"{'You can now enter giveaways!' if msgs >= GIVEAWAY_MIN_MESSAGES else f'You need {GIVEAWAY_MIN_MESSAGES-msgs} more messages to enter giveaways.'}\nCheck your progress: `!rank`")
    except: pass

@bot.command(name="deny")
@commands.has_permissions(manage_roles=True)
async def deny(ctx, member: discord.Member = None, *, reason: str = "Could not verify your Kakobuy account."):
    if not member:
        await ctx.send("❌ Usage: `!deny @user [reason]`", delete_after=8); return
    await ctx.send(f"❌ Verification denied for {member.mention}.")
    try:
        await member.send(f"❌ **Verification denied on Swag WRLD.**\nReason: {reason}\n\nRegister via our link first → {KAKOBUY_LINK}\nThen `!verify [username]` again.")
    except: pass

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id: return
    guild = bot.get_guild(payload.guild_id)
    if not guild: return

    # Giveaway eligibility check
    gw = get_active_giveaway(str(payload.message_id))
    if gw and str(payload.emoji) == "🎉":
        reactor = guild.get_member(payload.user_id)
        if reactor and not reactor.bot:
            uid = str(payload.user_id)
            user = get_user(uid)
            kakobuy_role = guild.get_role(ROLE_KAKOBUY)
            has_role = bool(kakobuy_role and kakobuy_role in reactor.roles)
            msgs = user.get("messages", 0)
            has_msgs = msgs >= GIVEAWAY_MIN_MESSAGES
            if not has_role or not has_msgs:
                try:
                    await reactor.send(
                        f"❌ **You're not eligible for this Swag WRLD giveaway.**\n\n"
                        f"**Prize:** {gw.get('prize','Unknown')}\n\n"
                        f"To be eligible you need:\n"
                        f"• The **🛒 Kakobuy Member** role\n"
                        f"  Register here → {KAKOBUY_LINK}\n"
                        f"  Then type `!verify [your kakobuy username]` in the server\n"
                        f"• At least **{GIVEAWAY_MIN_MESSAGES} messages** sent in the server\n\n"
                        f"You can participate on the next one ;)!\nGet ready → `!rank` to check your progress 🔥")
                except:
                    ch = guild.get_channel(payload.channel_id)
                    if ch:
                        await ch.send(f"{reactor.mention} ❌ You're not eligible for this giveaway yet — check your DMs or use `!rank`.", delete_after=15)
        return

    # Verify request reactions
    ch = guild.get_channel(payload.channel_id)
    if not ch or ch.id != VERIFY_CHANNEL: return
    reactor = guild.get_member(payload.user_id)
    if not reactor or not reactor.guild_permissions.manage_roles: return
    msg = await ch.fetch_message(payload.message_id)
    if not msg.embeds or "Verification Request" not in (msg.embeds[0].title or ""): return
    footer = msg.embeds[0].footer.text or ""
    try: target_id = int(footer.replace("User ID:","").strip()); target = guild.get_member(target_id)
    except: return

    if str(payload.emoji) == "✅" and target:
        role = guild.get_role(ROLE_KAKOBUY)
        if role and role not in target.roles:
            await target.add_roles(role)
            msgs = get_user(str(target_id)).get("messages", 0)
            await ch.send(f"✅ {target.mention} approved by {reactor.mention} → **🛒 Kakobuy Member** given.\nMessages: {msgs}/{GIVEAWAY_MIN_MESSAGES}")
            try:
                await target.send(
                    f"✅ **Verified on Swag WRLD!** You now have the **🛒 Kakobuy Member** role 🔥\n"
                    f"{'✅ Giveaway eligible!' if msgs >= GIVEAWAY_MIN_MESSAGES else f'⚠️ You need {GIVEAWAY_MIN_MESSAGES-msgs} more messages to enter giveaways.'}\nCheck your stats with `!rank`.")
            except: pass
    elif str(payload.emoji) == "❌" and target:
        await ch.send(f"❌ {target.mention} denied by {reactor.mention}.")
        try:
            await target.send(f"❌ **Verification denied on Swag WRLD.**\nMake sure you registered using our link → {KAKOBUY_LINK}\nThen try `!verify [username]` again.")
        except: pass

@bot.command(name="giveaway", aliases=["gw"])
@commands.has_permissions(manage_guild=True)
async def giveaway(ctx, duration_minutes: int = None, *, prize: str = None):
    if not duration_minutes or not prize:
        await ctx.send("❌ Usage: `!giveaway [minutes] [prize]`", delete_after=10); return
    end_time = datetime.now() + timedelta(minutes=duration_minutes)
    kakobuy_role = ctx.guild.get_role(ROLE_KAKOBUY)
    e = discord.Embed(title="🎉 GIVEAWAY",
        description=(f"**Prize:** {prize}\n\nReact with 🎉 to enter!\n\n"
            f"**Requirements to win:**\n✅ **🛒 Kakobuy Member** role — `!verify [username]`\n"
            f"✅ At least **{GIVEAWAY_MIN_MESSAGES} messages** sent in server\n\n"
            f"💜 **Server Boosters get 3 extra entries!**\n\nNo account? Register → {KAKOBUY_LINK}\n\n"
            f"**Ends:** <t:{int(end_time.timestamp())}:R>"), color=0x111111)
    e.set_footer(text="Swag WRLD Giveaway • Good luck!")
    msg = await ctx.send(embed=e)
    await msg.add_reaction("🎉")
    save_giveaway(str(msg.id), prize, end_time.timestamp(), ctx.channel.id)
    await asyncio.sleep(duration_minutes * 60)

    msg = await ctx.channel.fetch_message(msg.id)
    reaction = discord.utils.get(msg.reactions, emoji="🎉")
    all_users = [u async for u in reaction.users() if not u.bot]

    eligible, no_role, low_msgs = [], 0, 0
    for u in all_users:
        m = ctx.guild.get_member(u.id)
        if not m: continue
        ud = get_user(str(u.id))
        has_role = (not GIVEAWAY_NEEDS_KAKOBUY) or (kakobuy_role and kakobuy_role in m.roles)
        has_msgs = ud.get("messages", 0) >= GIVEAWAY_MIN_MESSAGES
        if has_role and has_msgs:
            entries = 4 if m.premium_since else 1
            eligible.extend([u] * entries)
        else:
            if not has_role: no_role += 1
            if not has_msgs: low_msgs += 1

    unique_eligible = len(set(u.id for u in eligible))
    delete_giveaway(str(msg.id))

    if not eligible:
        e2 = discord.Embed(title="😢 Giveaway Over — No Eligible Winners",
            description=(f"**{len(all_users)}** entered, none were eligible.\n"
                f"• {no_role} missing **🛒 Kakobuy Member** role\n• {low_msgs} under **{GIVEAWAY_MIN_MESSAGES} messages**\n\n"
                f"Register → {KAKOBUY_LINK} + `!verify` + keep chatting!"), color=0x111111)
        await ctx.send(embed=e2); return

    winner = random.choice(eligible)
    is_booster = ctx.guild.get_member(winner.id).premium_since is not None
    e3 = discord.Embed(title="🏆 Giveaway Over!",
        description=(f"**Prize:** {prize}\n\n🎉 Congratulations {winner.mention} — you won!{'  💜 (Nitro Booster bonus entries paid off!)' if is_booster else ''}\n"
            f"DM a mod to claim your prize.\n\n*{len(all_users)} entered • {unique_eligible} eligible*"), color=0x111111)
    await ctx.send(embed=e3)

@bot.command(name="boostxp")
@commands.has_permissions(manage_guild=True)
async def boost_xp(ctx, minutes: int = 60):
    end_ts = datetime.now().timestamp() + (minutes * 60)
    set_setting("boost_ends", end_ts)
    e = discord.Embed(title="⚡ DOUBLE XP EVENT",
        description=f"All messages earn **2x XP** for the next **{minutes} minutes**!\nEnds: <t:{int(end_ts)}:R>\n\nChat now to level up faster 🔥", color=0x111111)
    await ctx.send(embed=e)
    await asyncio.sleep(minutes * 60)
    set_setting("boost_ends", 0)
    await ctx.send("⚡ Double XP event ended. Back to normal!")

@bot.command(name="givexp")
@commands.has_permissions(manage_guild=True)
async def give_xp(ctx, member: discord.Member = None, amount: int = None):
    if not member or not amount:
        await ctx.send("❌ Usage: `!givexp @user [amount]`", delete_after=8); return
    uid = str(member.id)
    user = get_user(uid)
    old_lvl = compute_level(user["xp"])[0]
    user["xp"] += amount
    new_lvl = compute_level(user["xp"])[0]
    save_user(user)
    if new_lvl > old_lvl: await apply_level_roles(member, new_lvl)
    await ctx.send(f"✅ Gave **{amount:,} XP** to {member.mention} → Level {new_lvl}.")

@bot.command(name="serverinfo", aliases=["si"])
async def server_info(ctx):
    total_xp   = list(users_col.aggregate([{"$group":{"_id":None,"total":{"$sum":"$xp"}}}]))
    total_msgs = list(users_col.aggregate([{"$group":{"_id":None,"total":{"$sum":"$messages"}}}]))
    xp_val  = total_xp[0]["total"] if total_xp else 0
    msg_val = total_msgs[0]["total"] if total_msgs else 0
    verified = sum(1 for m in ctx.guild.members if ctx.guild.get_role(ROLE_KAKOBUY) and ctx.guild.get_role(ROLE_KAKOBUY) in m.roles)
    e = discord.Embed(title="📊 Swag WRLD Server Stats", color=0x111111)
    e.add_field(name="👥 Members",        value=f"{ctx.guild.member_count:,}", inline=True)
    e.add_field(name="🛒 Verified",       value=f"{verified:,}", inline=True)
    e.add_field(name="⭐ Total XP",       value=f"{xp_val:,}", inline=True)
    e.add_field(name="📨 Total Messages", value=f"{msg_val:,}", inline=True)
    e.add_field(name="⚡ 2x XP",         value="Active" if is_boost_active() else "Off", inline=True)
    e.set_footer(text="Swag WRLD")
    await ctx.send(embed=e)

@bot.command(name="help")
async def help_cmd(ctx):
    e = discord.Embed(title="🤖 Swag WRLD Bot — Commands", color=0x111111)
    e.add_field(name="👤 Everyone", value=(
        "`!checkin` — Daily streak + bonus XP\n`!rank [@user]` — Level, XP, streak, eligibility\n"
        "`!leaderboard` — All-time top 10\n`!leaderboard week` — This week's most active\n"
        "`!verify [username]` — Submit Kakobuy verification\n`!serverinfo` — Server activity overview"), inline=False)
    e.add_field(name="🛡️ Mods Only", value=(
        "`!approve @user` — Approve verification\n`!deny @user [reason]` — Deny verification\n"
        "`!giveaway [mins] [prize]` — Run a giveaway\n`!boostxp [mins]` — Double XP event\n"
        "`!givexp @user [amount]` — Give XP manually"), inline=False)
    e.add_field(name="📈 XP System", value=(
        f"• Chat → **{XP_MIN}–{XP_MAX} XP**/msg • 60s cooldown\n• First msg each day = **+{XP_DAILY_BONUS} bonus XP** ☀️\n"
        f"• `!checkin` = **+75–300 XP** depending on streak\n"
        "• Level 5 → 💬 Regular  •  Level 10 → 🔥 OG  •  Level 20 → 👑 VIP"), inline=False)
    e.add_field(name="🎉 Giveaway Requirements", value=f"**🛒 Kakobuy Member** role + **{GIVEAWAY_MIN_MESSAGES} messages** sent in server", inline=False)
    e.set_footer(text="Swag WRLD • powered by steinsswag")
    await ctx.send(embed=e)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission for that.", delete_after=8)
    elif isinstance(error, commands.CommandNotFound): pass
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid argument — check `!help`.", delete_after=8)
    else: raise error

bot.run(TOKEN)
