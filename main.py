import os
import re
import json
import time
import random
import asyncio
import traceback
from datetime import datetime, timedelta, timezone
from rubka import Robot, Message

# ═════════════════════════════════════════════════════════════
# FLUXBOT v3 — Special Users + Fixed Profanity + Fixed Long Message
# ═════════════════════════════════════════════════════════════

TOKEN = os.getenv("RUBIKA_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("❌ RUBIKA_TOKEN تنظیم نشده است.")

bot = Robot(token=TOKEN)
OWNER_ID = "u0KUJo1004f46bafc48e536f282693b6"
BOT_ID_CACHE = {"id": None}
IRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def get_local_now():
    return datetime.now(IRAN_TZ)


DATA_PATHS = [
    "/data/bot_data.json", "/app/data/bot_data.json",
    "/app/bot_data.json", "/tmp/bot_data.json", "./bot_data.json",
]
for _p in ("/data", "/app/data", "/tmp"):
    try: os.makedirs(_p, exist_ok=True)
    except: pass


# ═══════════════════ persistence ═══════════════════
def _read_json(paths, default):
    for _p in paths:
        try:
            if not os.path.isfile(_p): continue
            with open(_p, "r", encoding="utf-8") as _f:
                _v = json.load(_f)
            if isinstance(default, dict) and isinstance(_v, dict):
                return _v
        except Exception as _e:
            print(f"⚠️ read {_p}: {_e}", flush=True)
    return default.copy() if isinstance(default, dict) else default


def _write_json(paths, value):
    for _p in paths:
        _tmp = None
        try:
            _d = os.path.dirname(_p) or "."
            os.makedirs(_d, exist_ok=True)
            _tmp = _p + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as _f:
                json.dump(value, _f, ensure_ascii=False, indent=2)
                _f.flush()
                try: os.fsync(_f.fileno())
                except: pass
            os.replace(_tmp, _p)
            return True
        except Exception as _e:
            if _tmp and os.path.exists(_tmp):
                try: os.remove(_tmp)
                except: pass
    return False


_DEFAULT_SETTINGS = {
    "link": False,
    "id": False,
    "spam": True,
    "profanity": True,
    "kodhengi": False,
    "welcome": True,
}
_DEFAULT_DATA = {
    "settings": {}, "started_users": [], "known_groups": [], "known_users": {},
    "custom_welcome": {}, "welcomed_users": {},
    "games": {}, "bot_is_active": False,
    "special_users": {},
}

bot_data = _read_json(DATA_PATHS, {})
if not isinstance(bot_data, dict): bot_data = {}
for _k, _v in _DEFAULT_DATA.items():
    if _k not in bot_data or not isinstance(bot_data.get(_k), type(_v)):
        bot_data[_k] = _v.copy() if isinstance(_v, (dict, list)) else _v
settings = bot_data["settings"]
for _k, _v in _DEFAULT_SETTINGS.items():
    settings.setdefault(_k, _v)
bot_data["settings"] = settings
bot_is_active = bool(bot_data.get("bot_is_active", False))


def save_data(data=None):
    try:
        return _write_json(DATA_PATHS, data if isinstance(data, dict) else bot_data)
    except Exception as _e:
        print(f"⚠️ save: {_e}", flush=True)
        return False

save_data(bot_data)


# ═══════════════════ constants ═══════════════════
GAME_TIMEOUT = 120
SPAM_WINDOW = 5
SPAM_LIMIT = 5
EMPTY, RED, YELLOW = "⚫", "🔴", "🟡"

# آستانه‌های پیام طولانی (کد هنگی)
KODHENGI_MAX_LEN = 1500
KODHENGI_MAX_SAME_CHAR = 30
KODHENGI_MAX_LINES = 100
KODHENGI_MAX_LINE_LEN = 500

username_cache = {}
username_cache_ttl = {}
spam_tracker = {}
coin_flip_games = {}


# ═══════════════════ helpers ═══════════════════
def _strip_zwnj(s):
    if not s: return ""
    s = s.replace('\u200c', '').replace('\u200d', '').lower().strip()
    return s.strip('!.?؟،,؛;:«»"\'()[]{}<>-—–…*_~')


FILTER_ON = {_strip_zwnj(w) for w in {"فعال", "روشن", "قفل", "بسته", "بستن", "ببند", "on", "enable"}}
FILTER_OFF = {_strip_zwnj(w) for w in {"غیرفعال", "خاموش", "باز", "بازکردن", "بازکن", "off", "disable"}}


def match_two_word_cmd(text, word):
    if not text: return None
    t = text.replace('\u200c', ' ').replace('\u200d', ' ')
    parts = [_strip_zwnj(p) for p in t.strip().split() if p.strip()]
    w_parts = [_strip_zwnj(p) for p in word.replace('\u200c', ' ')
                                          .replace('\u200d', ' ').split() if p.strip()]
    if not w_parts or not parts: return None
    n = len(w_parts)
    for i in range(len(parts) - n + 1):
        if parts[i:i + n] != w_parts: continue
        rest = [r for r in (parts[:i] + parts[i + n:]) if r]
        if len(rest) == 1:
            if rest[0] in FILTER_ON: return True
            if rest[0] in FILTER_OFF: return False
        elif len(rest) == 2:
            j = rest[0] + rest[1]
            if j in FILTER_ON: return True
            if j in FILTER_OFF: return False
    return None


def is_group_chat(cid):
    return bool(cid) and str(cid).lower().startswith("g")


def is_private_chat(cid):
    if not cid: return False
    c = str(cid).lower()
    return c.startswith("u") or c.startswith("b")


def clean_message(text):
    if not text: return ""
    text = re.sub(r"@\S+", "", text)
    text = re.sub(r"(?<![/\w])/(\w+)", r"\1", text)
    return text.strip()


def extract_msg_id(result):
    try:
        if isinstance(result, dict):
            d = result.get("data", result)
            if isinstance(d, dict) and d.get("message_id"):
                return str(d["message_id"])
            if result.get("message_id"):
                return str(result["message_id"])
        elif hasattr(result, "message_id"):
            return str(result.message_id)
    except: pass
    return None


# ═══════════════════ reply target finder ═══════════════════
def extract_reply_info(message):
    """استخراج sender_id و reply_id از پیام"""
    result = {"reply_id": None, "sender_id": None}
    try:
        rt = (getattr(message, 'reply_to_message', None) or
              getattr(message, 'reply_message', None) or
              getattr(message, 'reply_to', None))
        if rt:
            if isinstance(rt, dict):
                mid = rt.get("message_id") or rt.get("messageId")
                if mid: result["reply_id"] = str(mid)
                sid = rt.get("sender_id") or rt.get("user_id") or rt.get("from_id")
                if not sid and isinstance(rt.get("sender"), dict):
                    sid = rt["sender"].get("id") or rt["sender"].get("user_id")
                if not sid and isinstance(rt.get("from"), dict):
                    sid = rt["from"].get("id") or rt["from"].get("user_id")
                if sid: result["sender_id"] = str(sid)
            else:
                mid = getattr(rt, "message_id", None) or getattr(rt, "messageId", None)
                if mid: result["reply_id"] = str(mid)
                sid = (getattr(rt, "sender_id", None) or
                       getattr(rt, "user_id", None) or
                       getattr(rt, "from_id", None))
                if not sid:
                    for _attr in ("sender", "from_user", "from", "user"):
                        sender = getattr(rt, _attr, None)
                        if sender:
                            if isinstance(sender, dict):
                                sid = sender.get("id") or sender.get("user_id")
                            else:
                                sid = getattr(sender, "id", None) or getattr(sender, "user_id", None)
                            if sid: break
                if sid: result["sender_id"] = str(sid)
    except Exception as e:
        print(f"⚠️ reply parse: {e}", flush=True)

    if not result["reply_id"]:
        for attr in ('reply_to_message_id', 'reply_message_id', 'reply_id'):
            try:
                val = getattr(message, attr, None)
                if val:
                    if hasattr(val, 'message_id'):
                        result["reply_id"] = str(val.message_id)
                    else:
                        result["reply_id"] = str(val)
                    break
            except: pass
    return result


async def find_reply_target(message, chat_id):
    """گرفتن sender_id کاربری که بهش ریپلای شده"""
    info = extract_reply_info(message)
    if info["sender_id"] and info["sender_id"] not in ("", "None", "none", "null", "0"):
        return info["sender_id"]
    if info["reply_id"] and chat_id:
        try:
            msg = await bot.get_message(chat_id=chat_id, message_id=info["reply_id"])
            if msg:
                if isinstance(msg, dict):
                    d = msg.get('data', msg)
                    m = d.get('message', d) if isinstance(d, dict) else {}
                    sid = m.get('sender_id') or m.get('user_id') or m.get('from_id')
                    if sid: return str(sid)
                elif hasattr(msg, 'sender_id'):
                    return str(msg.sender_id)
        except Exception as e:
            print(f"⚠️ find_reply: {e}", flush=True)
    return None


# ═══════════════════ user / role ═══════════════════
async def get_user_info(chat_id, user_id, force=False):
    key = f"{chat_id}:{user_id}"
    now = time.time()
    cached = username_cache.get(key)
    if not force and cached and now < username_cache_ttl.get(key, 0):
        return cached
    info = {"name": None, "role": "عضو"}
    try:
        mi = await bot.get_chat_member(chat_id, user_id)
        if mi:
            data = mi
            for _ in range(3):
                if isinstance(data, dict) and isinstance(data.get("data"), dict):
                    data = data["data"]
                else: break
            if not isinstance(data, dict):
                try: data = vars(data)
                except: data = {}
            member = data.get("chat_member") or data.get("member") or data
            if not isinstance(member, dict):
                try: member = vars(member)
                except: member = {}
            user = member.get("user") or data.get("user") or {}
            if not isinstance(user, dict):
                try: user = vars(user)
                except: user = {}
            def _pick(*keys):
                for src in (member, user, data):
                    if not isinstance(src, dict): continue
                    for k in keys:
                        v = src.get(k)
                        if v is None or v is False or v == "": continue
                        if isinstance(v, (dict, list)) and not v: continue
                        return v
                return None
            info["name"] = _pick("first_name", "firstName", "name", "title", "display_name")
            st = str(_pick("status", "member_type", "memberType", "role", "type")
                     or "").strip().lower().replace("_", "").replace("-", "")
            if st in ("creator", "owner", "admincreator", "superadmin", "chatcreator"):
                info["role"] = "مالک"
            elif st in ("admin", "administrator", "moderator", "mod"):
                info["role"] = "ادمین"
            elif _pick("is_creator", "isCreator", "is_owner", "isOwner"):
                info["role"] = "مالک"
            elif _pick("is_admin", "isAdmin"):
                info["role"] = "ادمین"
            username_cache[key] = info
            username_cache_ttl[key] = now + 7200
    except Exception as e:
        print(f"⚠️ get_user_info: {e}", flush=True)
    return info


def _safe_name(v, uid=None):
    if not v: return None
    name = re.sub(r"(?<![\w])@[A-Za-z0-9_.]+", "", str(v)).strip(" \t\r\n,،")
    if not name or (uid is not None and name == str(uid)): return None
    return name


def get_display_for_user(uid):
    uid = str(uid)
    ku = bot_data.get("known_users", {}).get(uid, {})
    return (_safe_name(ku.get("name"), uid) if isinstance(ku, dict) else None) or "کاربر"


def format_user_display(ui, uid):
    ui = ui if isinstance(ui, dict) else {}
    return _safe_name(ui.get("name"), uid) or get_display_for_user(uid)


async def is_actual_group_owner(chat_id, sender_id):
    try:
        info = await bot.get_chat_info(chat_id)
        if not isinstance(info, dict): return False
        d = info.get("data", info)
        if not isinstance(d, dict): return False
        c = d.get("chat", d)
        if not isinstance(c, dict): return False
        owner = c.get("owner_id") or c.get("creator_id") or c.get("owner")
        if owner and str(owner) == str(sender_id):
            return True
    except: pass
    return False


# ═══════════════════ content detection ═══════════════════
_URL_PATTERN = re.compile(
    r"(?i)(?:https?://|ftp://|www\.)\S+"
    r"|(?<![\w@.\-])(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+"
    r"(?:com|net|org|ir|info|io|co|me|tv|xyz|online|site|app|dev|ai|tech|cloud|"
    r"edu|gov|ru|de|uk|fr|jp|cn|kr|in|pk|af|tr|iq|sa|ae|qa|kw|bh|om|ye|jo|lb|"
    r"sy|ps|eg|ly|tn|dz|ma|shop|store|blog|pro|name|mobi|asia|travel|link|click|top|win)"
    r"(?::\d{1,5})?(?:[/?#]\S*)?"
)


def contains_link(text):
    return bool(text and _URL_PATTERN.search(str(text)))


def contains_id(text):
    if not text: return False
    if re.search(r'@[\w\.\-]+', text): return True
    if re.search(r'@\[[^\]]+\]', text): return True
    if re.search(r'(?<![A-Za-z0-9])[ub][A-Za-z0-9]{15,}(?![A-Za-z0-9])', text): return True
    return False


# ─────────── کد هنگی (پیام طولانی/هنگ‌کننده) ───────────
def contains_kodhengi(text):
    """
    تشخیص پیام‌های طولانی یا هنگ‌کننده:
    - متن بیشتر از ۱۵۰۰ کاراکتر
    - تکرار یک کاراکتر ۳۰+ بار پشت هم (a...، !!!...، 😂😂😂...)
    - بیشتر از ۱۰۰ خط
    - یک خط بیشتر از ۵۰۰ کاراکتر
    """
    if not text: return False
    s = str(text)
    if len(s) > KODHENGI_MAX_LEN:
        return True
    if re.search(r'(.)\1{' + str(KODHENGI_MAX_SAME_CHAR) + r',}', s, re.DOTALL):
        return True
    if s.count('\n') > KODHENGI_MAX_LINES:
        return True
    for line in s.split('\n'):
        if len(line) > KODHENGI_MAX_LINE_LEN:
            return True
    return False


# ─────────── فحش (بازنویسی دقیق) ───────────
def _normalize_persian(text):
    """نرمال‌سازی: یکسان‌سازی ی/ک، حذف اعراب، حذف ZWNJ"""
    t = str(text).lower()
    t = t.replace('ك', 'ک').replace('ي', 'ی').replace('ۀ', 'ه').replace('ة', 'ه')
    t = re.sub(r'[\u064B-\u0652]', '', t)
    t = t.replace('\u200c', '').replace('\u200d', '')
    return t


# فحش‌های خیلی بد — تطبیق کامل کلمه (بالاترین دقت)
_PROFANITY_EXACT = {
    "کیر", "کص", "کون",
    "کیرم", "کیرت", "کیرش", "کیرتو", "کیرمو", "کیرمو", "کیرمو",
    "کصم", "کصت", "کصش", "کصتو", "کصمو",
    "کونم", "کونت", "کونش", "کونتو", "کونمو",
    "کیرکش", "کیرخور", "کیرمغز", "کیرخور",
    "کصکش", "کسکش", "کصخل", "کسخل", "کصخور", "خارکصه", "خارکصده",
    "کونکش", "کونخور", "کونی", "کونده", "کونیا",
    "جنده", "جاکش", "حرومزاده", "حرومزاد", "حرومزادگی",
    "دیوث", "دیوثه", "بیناموس", "بیناموست",
    "پدرسوخته", "مادرسوخته", "لاشی", "لاشخور",
    "کثافت", "قرمساق", "پفیوز",
}

# ریشه‌های پرخطر — تطبیق زیررشته‌ای (کلمات مرکب رو می‌گیره)
_PROFANITY_ROOTS = {
    "کیر", "کص", "کون",
    "جنده", "دیوث",
    "کیرکش", "کیرخور", "کیرمغز",
    "کصکش", "کسکش", "کصخل", "کسخل", "کصخور", "خارکصه",
    "کونکش", "کونخور", "کونده",
    "حرومزاد", "بیناموس",
    "پدرسوخته", "مادرسوخته",
    "لاشی", "لاشخور", "کثافت", "قرمساق", "پفیوز", "جاکش",
}

# کلمات امن که ممکنه ریشه رو داشته باشن ولی فحش نیستن
_SAFE_WORDS = {
    "کیرلا", "کیرل", "کیرالا",  # Kerala
    "کونگ", "کونگفو", "کونگف", "کونیا", "کونکورد", "کونگور", "کونانی",
    "کسب", "کسبوکار", "کسبو", "واکسن", "تاکسی", "اکسیر", "اکس", "کسری",
    "کسی", "کسان", "کسانی", "چکسی", "هرکسی", "همکسی", "هیچکسی",
    "کتاب", "کنکور", "کانون",
    "کیرو",  # اسم خاص
}


def contains_profanity(text):
    """
    تشخیص فحش دقیق:
    1. تطبیق کامل کلمه با لیست دقیق
    2. تطبیق زیررشته‌ای برای ریشه‌های پرخطر (به‌جز کلمات امن)
    هیچ‌گاه کلمات عادی مثل «کتاب»، «کسی»، «مادرت»، «احمق» را نمی‌گیرد.
    """
    if not text: return False
    t = _normalize_persian(text)
    tokens = re.findall(r'[آ-ی]+', t)
    if not tokens: return False

    # مرحله ۱: تطبیق کامل
    for tok in tokens:
        if tok in _PROFANITY_EXACT:
            return True

    # مرحله ۲: زیررشته‌ای برای ریشه‌های قوی
    for tok in tokens:
        if tok in _SAFE_WORDS:
            continue
        for root in _PROFANITY_ROOTS:
            if root in tok:
                return True

    return False


# ═══════════════════ state helpers ═══════════════════
def set_lock(key, value):
    settings[key] = bool(value)
    bot_data["settings"] = settings
    save_data(bot_data)


def get_welcome_text(chat_id, chat_name, display_name):
    custom = bot_data.get("custom_welcome", {}).get(chat_id)
    now_str = get_local_now().strftime("%H:%M - %Y/%m/%d")
    if custom:
        return (custom.replace("{name}", display_name)
                .replace("{group}", chat_name).replace("{time}", now_str))
    return (
        f"╭─━━━━━━━━━━━━━━━━━━━─╮\n   ⚡ **FLUXBOT** ⚡\n   🌊 جریان قدرت 🌊\n╰─━━━━━━━━━━━━━━━━━━━─╯\n\n"
        f"🌟 **به گروه {chat_name} خوش آمدید!** 🌟\n\n"
        f"👤 **{display_name} عزیز:**\nخوشحالیم که به ما پیوستید. 🌹\n\n"
        f"⏰ **ورود:** {now_str}\n\n⚡ **FLUXBOT**"
    )


async def get_chat_name(chat_id):
    try:
        info = await bot.get_chat_info(chat_id)
        for _ in range(4):
            if isinstance(info, dict) and isinstance(info.get("data"), dict):
                info = info["data"]
            else: break
        if isinstance(info, dict):
            c = info.get("chat", info)
            if isinstance(c, dict):
                for k in ("title", "name", "chat_name", "group_name"):
                    if c.get(k): return str(c[k])
    except: pass
    return "گروه"


# ═══════════════════ spam ═══════════════════
async def handle_spam_check(chat_id, sender_id, msg_id):
    now = time.time()
    spam_tracker.setdefault(chat_id, {}).setdefault(sender_id, [])
    bucket = spam_tracker[chat_id][sender_id]
    bucket.append({"t": now, "id": str(msg_id)})
    bucket[:] = [e for e in bucket if now - e["t"] < SPAM_WINDOW]

    if len(bucket) >= SPAM_LIMIT:
        for e in bucket:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=e["id"])
                await asyncio.sleep(0.08)
            except: pass
        spam_tracker[chat_id][sender_id] = []
        return True
    return False


# ═══════════════════ Connect 4 ═══════════════════
def render_board(board):
    lines = ["1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣"]
    for row in board:
        lines.append("".join(EMPTY if c is None else (RED if c == "R" else YELLOW) for c in row))
    return "\n".join(lines)


def drop_piece(board, col, color):
    for row in range(5, -1, -1):
        if board[row][col] is None:
            board[row][col] = color
            return True, row, col
    return False, -1, -1


def check_win(board, row, col, color):
    for dr, dc in [(0, 1), (1, 0), (1, 1), (1, -1)]:
        cnt = 1
        r, c = row + dr, col + dc
        while 0 <= r < 6 and 0 <= c < 7 and board[r][c] == color:
            cnt += 1; r += dr; c += dc
        r, c = row - dr, col - dc
        while 0 <= r < 6 and 0 <= c < 7 and board[r][c] == color:
            cnt += 1; r -= dr; c -= dc
        if cnt >= 4: return True
    return False


def is_board_full(board):
    return all(cell is not None for row in board for cell in row)


def get_games():
    if not isinstance(bot_data.get("games"), dict):
        bot_data["games"] = {}
    return bot_data["games"]


async def game_timeout_check(chat_id, started_at):
    try:
        await asyncio.sleep(GAME_TIMEOUT)
        games = get_games()
        g = games.get(chat_id)
        if g and g.get("started_at") == started_at and g.get("status") == "waiting":
            del games[chat_id]
            save_data(bot_data)
            try:
                await bot.send_message(chat_id=chat_id,
                    text="⏱️ **زمان بازی به پایان رسید!**\n\nکسی شرکت نکرد.\n\n⚡ **FLUXBOT**")
            except: pass
    except: pass


async def coinflip_timeout(chat_id, started_at):
    try:
        await asyncio.sleep(GAME_TIMEOUT)
        g = coin_flip_games.get(chat_id)
        if g and g.get("started_at") == started_at:
            del coin_flip_games[chat_id]
            try:
                await bot.send_message(chat_id=chat_id,
                    text="⏱️ **زمان انتخاب به پایان رسید!**\n\nبازی لغو شد.\n\n⚡ **FLUXBOT**")
            except: pass
    except: pass


# ═══════════════════ main handler ═══════════════════
@bot.on_message()
async def handle_message(bot, message):
    global bot_is_active, bot_data, settings

    try:
        chat_id = str(message.chat_id) if message.chat_id else ""
        sender_id = str(message.sender_id) if message.sender_id else ""

        raw_text = ""
        t = getattr(message, "text", None)
        if t:
            raw_text = t.strip()
        else:
            cap = (getattr(message, "caption", None)
                   or getattr(message, "text_caption", None) or "")
            if cap: raw_text = str(cap).strip()
        clean_text = clean_message(raw_text)

        try:
            mid_raw = getattr(message, "message_id", None)
            msg_id = str(mid_raw) if mid_raw is not None else ""
        except: msg_id = ""

        # ثبت کاربر/گروه
        if sender_id and sender_id not in (bot_data.get("started_users") or []):
            bot_data.setdefault("started_users", []).append(sender_id)
            save_data(bot_data)
        if is_group_chat(chat_id) and chat_id not in (bot_data.get("known_groups") or []):
            bot_data.setdefault("known_groups", []).append(chat_id)
            save_data(bot_data)

        print(f"📩 {chat_id} | {sender_id} | {raw_text!r}", flush=True)

        # ═══════ پیوی: هیچ جوابی نمیده ═══════
        if not is_group_chat(chat_id):
            return

        if sender_id and sender_id == BOT_ID_CACHE.get("id"): return

        # ═══════ نقش و مجوز ═══════
        ui = await get_user_info(chat_id, sender_id)
        role = ui["role"]
        disp = format_user_display(ui, sender_id)
        is_special = bot_data.get("special_users", {}).get(chat_id, {}).get(sender_id, False)
        can_manage = (str(sender_id) == OWNER_ID) or (role == "مالک") or is_special
        is_owner_group = (str(sender_id) == OWNER_ID) or (role == "مالک")

        # ═══════ فعال‌سازی ═══════
        if not bot_is_active:
            if clean_text in ("فعال", "فاعل"):
                if not can_manage:
                    if await is_actual_group_owner(chat_id, sender_id):
                        can_manage = True
                        is_owner_group = True
                        role = "مالک"
                        ui["role"] = "مالک"
                if can_manage:
                    bot_is_active = True
                    bot_data["bot_is_active"] = True
                    save_data(bot_data)
                    try:
                        await bot.send_message(chat_id=chat_id, text="✅ **ربات فعال شد.**",
                            reply_to_message_id=message.message_id)
                    except: pass
            return

        # ═══════ غیرفعال‌سازی ═══════
        if is_owner_group and clean_text == "غیرفعال":
            bot_is_active = False
            bot_data["bot_is_active"] = False
            save_data(bot_data)
            try:
                await bot.send_message(chat_id=chat_id, text="🛑 **ربات غیرفعال شد.**",
                    reply_to_message_id=message.message_id)
            except: pass
            return

        # ═══════ دستورات مدیریتی ═══════
        if can_manage:
            # ─── ویژه / ادمین ───
            if clean_text in ("ویژه", "ادمین"):
                tgt = await find_reply_target(message, chat_id)
                if not tgt:
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text="⚠️ برای ویژه کردن، روی پیام کاربر ریپلای کنید و بنویسید `ویژه`.")
                    return
                if str(tgt) == str(sender_id):
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text="⚠️ نمی‌توانید به خودتان ویژه بدهید.")
                    return
                if str(tgt) == OWNER_ID:
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text="ℹ️ مالک ربات از قبل همه‌کاره است.")
                    return
                specials = bot_data.setdefault("special_users", {}).setdefault(chat_id, {})
                if specials.get(str(tgt), False):
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text="ℹ️ این کاربر از قبل ویژه است.")
                    return
                specials[str(tgt)] = True
                save_data(bot_data)
                try:
                    _name = get_display_for_user(tgt)
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text=f"⭐ **{_name} ویژه شد!**\n\n"
                             f"✅ حالا می‌تونه تمام دستورات مدیریتی رو اجرا کنه.\n\n"
                             f"🔓 برای لغو: روی پیامش ریپلای کنید و بنویسید `حذف ویژه`\n\n⚡ **FLUXBOT**")
                except Exception as e:
                    print(f"⚠️ ویژه: {e}", flush=True)
                return

            # ─── حذف ویژه ───
            if clean_text in ("حذف ویژه", "لغو ویژه"):
                tgt = await find_reply_target(message, chat_id)
                if not tgt:
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text="⚠️ برای حذف ویژه، روی پیام کاربر ریپلای کنید.")
                    return
                specials = bot_data.get("special_users", {}).get(chat_id, {})
                if str(tgt) in specials:
                    del specials[str(tgt)]
                    save_data(bot_data)
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text="✅ **کاربر از ویژه خارج شد.**")
                else:
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text="ℹ️ این کاربر ویژه نبود.")
                return

            # ─── قفل‌های محتوا ───
            lock_cmds = {
                "لینک": "link",
                "آیدی": "id",
                "اسپم": "spam",
                "فحش": "profanity",
                "کد هنگی": "kodhengi",
                "کدهنگی": "kodhengi",
            }
            for word, key in lock_cmds.items():
                res = match_two_word_cmd(clean_text, word)
                if res is not None:
                    set_lock(key, res)
                    state = "🔒 قفل" if res else "🔓 باز"
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text=f"{state} **{word}** شد.\n\n⚡ **FLUXBOT**")
                    return

            # ─── لیست قفل ───
            if clean_text == "لیست قفل":
                def st(v): return "🔴 بسته" if v else "🟢 باز"
                def ft(v): return "🟢 فعال" if v else "🔴 غیرفعال"
                specials = bot_data.get("special_users", {}).get(chat_id, {})
                special_count = sum(1 for v in specials.values() if v)
                await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id, text=(
                    "📋 **لیست قفل‌ها**\n"
                    "━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🔗 **لینک:** {st(settings['link'])}\n"
                    f"🆔 **آیدی:** {st(settings['id'])}\n"
                    f"📢 **اسپم:** {st(settings['spam'])}\n"
                    f"🤬 **فحش:** {st(settings['profanity'])}\n"
                    f"🎁 **کد هنگی:** {st(settings['kodhengi'])}\n\n"
                    f"👋 **خوش‌آمدگویی:** {ft(settings.get('welcome', True))}\n"
                    f"⭐ **کاربران ویژه:** {special_count}\n\n"
                    "⚡ **FLUXBOT**"))
                return

            # ─── خوش‌آمدگویی ───
            for _w in ("خوش آمدگویی", "خوش‌آمدگویی", "خوشامد گویی", "خوشامدگویی"):
                res = match_two_word_cmd(clean_text, _w)
                if res is not None:
                    settings["welcome"] = res
                    bot_data["settings"] = settings
                    save_data(bot_data)
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text=f"✅ **خوش‌آمدگویی** {'🟢 فعال' if res else '🔴 غیرفعال'} شد.\n\n⚡ **FLUXBOT**")
                    return

            cwm = re.match(r"^تنظیم\s+پیام\s+خوش\s*آمدگویی\s+([\s\S]+)$", raw_text.strip())
            if cwm:
                wt = cwm.group(1).strip()
                if wt:
                    bot_data.setdefault("custom_welcome", {})[chat_id] = wt
                    save_data(bot_data)
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text="✅ **پیام خوش‌آمدگویی تنظیم شد!**\n\n"
                             "💡 متغیرها: `{name}` / `{group}` / `{time}`\n\n⚡ **FLUXBOT**")
                return

            if clean_text in ("حذف پیام خوش آمدگویی", "حذف پیام خوش‌آمدگویی"):
                if bot_data.get("custom_welcome", {}).pop(chat_id, None) is not None:
                    save_data(bot_data)
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text="✅ **پیام حذف شد.**")
                else:
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text="ℹ️ پیام سفارشی وجود ندارد.")
                return

            if clean_text in ("نمایش پیام خوش آمدگویی", "نمایش پیام خوش‌آمدگویی"):
                custom = bot_data.get("custom_welcome", {}).get(chat_id)
                if custom:
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text="📋 **پیام فعلی:**\n\n" + custom)
                else:
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text="ℹ️ پیام پیش‌فرض فعال است.")
                return

            # ─── راهنما ───
            if clean_text in ("راهنما", "کمک", "help"):
                await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id, text=(
                    "📚 **راهنمای FLUXBOT**\n"
                    "━━━━━━━━━━━━━━━━━━━\n\n"
                    "🔒 **قفل‌ها:**\n"
                    "├ `لینک قفل` / `لینک باز`\n"
                    "├ `آیدی قفل` / `آیدی باز`\n"
                    "├ `اسپم قفل` / `اسپم باز`\n"
                    "├ `فحش قفل` / `فحش باز`\n"
                    "├ `کد هنگی قفل` / `کد هنگی باز` (پیام‌های طولانی)\n"
                    "├ `خوش آمدگویی قفل` / `خوش آمدگویی باز`\n"
                    "└ `لیست قفل`\n\n"
                    "⭐ **ویژه:**\n"
                    "├ `ویژه` (ریپلای روی کاربر)\n"
                    "└ `حذف ویژه` (ریپلای روی کاربر)\n\n"
                    "👋 **خوش‌آمدگویی:**\n"
                    "├ `تنظیم پیام خوش آمدگویی [متن]`\n"
                    "├ `نمایش پیام خوش آمدگویی`\n"
                    "└ `حذف پیام خوش آمدگویی`\n\n"
                    "🎮 **بازی‌ها:**\n"
                    "├ `دوز` / `دوز قرمز` / `دوز زرد`\n"
                    "├ `شرکت` → پیوستن\n"
                    "├ `1` تا `7` → ستون\n"
                    "└ `بازی شیر یا خط`\n\n"
                    "⚡ **FLUXBOT**"))
                return

        # ═══════════════════════════════════════════════════
        # بررسی تخلف — فقط حذف، بدون اخطار/کیک/پیام
        # ═══════════════════════════════════════════════════
        if not can_manage:
            violation = None
            if settings.get("id") and contains_id(raw_text):
                violation = "ID"
            elif settings.get("link") and contains_link(raw_text):
                violation = "LINK"
            elif settings.get("profanity", True) and contains_profanity(raw_text):
                violation = "PROFANITY"
            elif settings.get("kodhengi") and contains_kodhengi(raw_text):
                violation = "KODHENGI"
            elif settings.get("spam", True) and msg_id:
                if await handle_spam_check(chat_id, sender_id, msg_id):
                    return

            if violation:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
                    print(f"🗑️ [{violation}] پاک شد | {chat_id}", flush=True)
                except Exception as e:
                    print(f"⚠️ delete {violation}: {e}", flush=True)
                return

        # ═══════ خوش‌آمدگویی ═══════
        if settings.get("welcome", True) and not can_manage:
            welcomed = bot_data.get("welcomed_users", {}).get(chat_id, {})
            if sender_id not in welcomed:
                cn = await get_chat_name(chat_id)
                try:
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text=get_welcome_text(chat_id, cn, disp))
                    bot_data.setdefault("welcomed_users", {}).setdefault(chat_id, {})[sender_id] = True
                    save_data(bot_data)
                except Exception as e:
                    print(f"⚠️ welcome: {e}", flush=True)

        games = get_games()

        # ═══════ حل بازی شیر یا خط ═══════
        if chat_id in coin_flip_games:
            cf = coin_flip_games[chat_id]
            if str(sender_id) == cf["player"] and raw_text.strip() in ("شیر", "خط"):
                choice = raw_text.strip()
                del coin_flip_games[chat_id]
                result = random.choice(["شیر", "خط"])
                won = (choice == result)
                if won:
                    text = (f"🪙 **نتیجه:** {result}\n\n"
                            f"🎉 **{disp} برنده شد!** 🎉\n\n"
                            f"━━━━━━━━━━━━━━━━━━━\n⚡ **FLUXBOT**")
                else:
                    text = (f"🪙 **نتیجه:** {result}\n\n"
                            f"😢 **{disp} باخت!**\n\n"
                            f"━━━━━━━━━━━━━━━━━━━\n⚡ **FLUXBOT**")
                await bot.send_message(chat_id=chat_id, text=text,
                    reply_to_message_id=message.message_id)
                return

        # ═══════ شروع شیر یا خط ═══════
        if clean_text in ("بازی شیر یا خط", "شیر یا خط", "شیریا خط"):
            if chat_id in coin_flip_games:
                cf = coin_flip_games[chat_id]
                if time.time() - cf.get("started_at", 0) > (GAME_TIMEOUT * 3):
                    del coin_flip_games[chat_id]
                else:
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text="⚠️ **یک بازی شیر یا خط در جریان است!**")
                    return
            if chat_id in games and games[chat_id].get("status") in ("waiting", "playing"):
                await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                    text="⚠️ **یک بازی دوز در جریان است!**")
                return
            started_at = time.time()
            coin_flip_games[chat_id] = {"player": sender_id, "started_at": started_at}
            try: asyncio.create_task(coinflip_timeout(chat_id, started_at))
            except: pass
            await bot.send_message(chat_id=chat_id, text=(
                f"🪙 **بازی شیر یا خط شروع شد!**\n\n"
                f"👤 **بازیکن:** {disp}\n\n"
                f"🎯 بنویس `شیر` یا `خط`\n\n"
                f"⏱️ ۲ دقیقه فرصت.\n\n⚡ **FLUXBOT**"))
            return

        # ═══════ شروع دوز ═══════
        game_match = re.match(r"^(دوز|بازی دوز)\s*(قرمز|زرد)?$", clean_text)
        if game_match:
            if chat_id in games and games[chat_id].get("status") in ("waiting", "playing"):
                g = games[chat_id]
                if time.time() - g.get("started_at", 0) > (GAME_TIMEOUT * 3):
                    del games[chat_id]; save_data(bot_data)
                else:
                    if g["status"] == "waiting":
                        await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                            text=f"⏳ **در انتظار حریف!**\n\n👤 **سازنده:** {g['player1_name']}\n📌 پیوستن: `شرکت`")
                    else:
                        await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                            text="⚠️ **یک بازی دوز در حال اجراست!**")
                    return
            if chat_id in coin_flip_games:
                await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                    text="⚠️ **یک بازی شیر یا خط در جریان است!**")
                return
            cc = game_match.group(2) or "قرمز"
            color1 = "R" if cc == "قرمز" else ("Y" if cc == "زرد" else "R")
            color2 = "Y" if color1 == "R" else "R"
            started_at = time.time()
            games[chat_id] = {
                "player1": sender_id, "player1_name": disp,
                "player2": None, "player2_name": None,
                "color1": color1, "color2": color2,
                "board": [[None] * 7 for _ in range(6)],
                "turn": sender_id, "status": "waiting", "started_at": started_at,
            }
            save_data(bot_data)
            try: asyncio.create_task(game_timeout_check(chat_id, started_at))
            except: pass
            ce = RED if color1 == "R" else YELLOW
            await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id, text=(
                f"🎮 **بازی دوز شروع شد!**\n\n"
                f"👤 **سازنده:** {disp}\n🎨 **رنگ:** {ce}\n\n"
                f"⏳ **منتظر حریف...**\n\n📌 پیوستن: `شرکت`\n"
                f"⏱️ ۲ دقیقه فرصت.\n\n⚡ **FLUXBOT**"))
            return

        # ═══════ شرکت در دوز ═══════
        if clean_text in ("شرکت", "join"):
            if chat_id not in games:
                await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                    text="⚠️ **بازی دوز فعالی نیست.**\n\nشروع: `دوز`")
                return
            g = games[chat_id]
            if g["status"] != "waiting":
                await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                    text="⚠️ **قبلاً شروع شده.**")
                return
            if g["player1"] == sender_id:
                await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                    text="⚠️ **نمی‌تونید به بازی خودتون بپیوندید!**")
                return
            g["player2"] = sender_id
            g["player2_name"] = disp
            g["status"] = "playing"
            g["turn"] = g["player1"]
            save_data(bot_data)
            board_txt = render_board(g["board"])
            c1 = RED if g["color1"] == "R" else YELLOW
            c2 = RED if g["color2"] == "R" else YELLOW
            await bot.send_message(chat_id=chat_id, text=(
                f"🎮 **بازی شروع شد!**\n\n"
                f"{c1} **{g['player1_name']}**  vs  {c2} **{g['player2_name']}**\n\n"
                f"```\n{board_txt}\n```\n\n"
                f"🎯 **نوبت:** {g['player1_name']} ({c1})\n\n"
                f"📌 عدد 1 تا 7 بفرست.\n\n⚡ **FLUXBOT**"))
            return

        # ═══════ انصراف ═══════
        if clean_text in ("انصراف", "لغو"):
            if chat_id in coin_flip_games and sender_id == coin_flip_games[chat_id]["player"]:
                del coin_flip_games[chat_id]
                await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                    text="🚫 **بازی شیر یا خط لغو شد.**")
                return
            if chat_id in games:
                g = games[chat_id]
                if sender_id in (g.get("player1"), g.get("player2")):
                    del games[chat_id]; save_data(bot_data)
                    await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                        text="🚫 **بازی دوز لغو شد.**")
                    return
            return

        # ═══════ حرکت در دوز ═══════
        if chat_id in games and games[chat_id].get("status") == "playing":
            g = games[chat_id]
            if sender_id in (g["player1"], g["player2"]):
                cm = re.match(r"^\s*([1-7])\s*$", raw_text)
                if cm:
                    if sender_id != g["turn"]:
                        nxt = g["player1_name"] if g["turn"] == g["player1"] else g["player2_name"]
                        await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                            text=f"⚠️ **نوبت شما نیست!**\n\n🎯 نوبت: **{nxt}**")
                        return
                    col = int(cm.group(1)) - 1
                    color = g["color1"] if sender_id == g["player1"] else g["color2"]
                    pname = g["player1_name"] if sender_id == g["player1"] else g["player2_name"]
                    ok, row, c = drop_piece(g["board"], col, color)
                    if not ok:
                        await bot.send_message(chat_id=chat_id, reply_to_message_id=message.message_id,
                            text="⚠️ **این ستون پر است!**")
                        return
                    ce = RED if color == "R" else YELLOW
                    if check_win(g["board"], row, c, color):
                        bt = render_board(g["board"])
                        del games[chat_id]; save_data(bot_data)
                        await bot.send_message(chat_id=chat_id,
                            text=f"🏆 **برنده!** 🏆\n\n{ce} **{pname}** برنده شد!\n\n"
                                 f"```\n{bt}\n```\n\n⚡ **FLUXBOT**")
                        return
                    if is_board_full(g["board"]):
                        bt = render_board(g["board"])
                        del games[chat_id]; save_data(bot_data)
                        await bot.send_message(chat_id=chat_id,
                            text=f"🤝 **مساوی!**\n\n```\n{bt}\n```\n\n⚡ **FLUXBOT**")
                        return
                    g["turn"] = g["player2"] if g["turn"] == g["player1"] else g["player1"]
                    save_data(bot_data)
                    bt = render_board(g["board"])
                    nn = g["player1_name"] if g["turn"] == g["player1"] else g["player2_name"]
                    nc = RED if ((g["turn"] == g["player1"] and g["color1"] == "R") or
                                 (g["turn"] == g["player2"] and g["color2"] == "R")) else YELLOW
                    await bot.send_message(chat_id=chat_id,
                        text=f"{ce} **{pname}** ستون {col + 1}.\n\n"
                             f"```\n{bt}\n```\n\n🎯 **نوبت:** {nn} ({nc})")
                    return

    except Exception as e:
        print(f"❌ HANDLER: {type(e).__name__}: {e}", flush=True)
        try: traceback.print_exc()
        except: pass


# ═══════════════════ background cleanup ═══════════════════
async def cleanup_task():
    while True:
        try:
            now = time.time()
            for k, exp in list(username_cache_ttl.items()):
                if exp <= now:
                    username_cache_ttl.pop(k, None)
                    username_cache.pop(k, None)
            for cid, users in list(spam_tracker.items()):
                for uid, entries in list(users.items()):
                    entries[:] = [e for e in entries if now - e["t"] < 60]
                    if not entries: users.pop(uid, None)
                if not users: spam_tracker.pop(cid, None)
        except Exception as e:
            print(f"⚠️ cleanup: {e}", flush=True)
        await asyncio.sleep(180)


# ═══════════════════ main ═══════════════════
async def main():
    print("🤖 FLUXBOT v3 STARTING...", flush=True)
    print(f"👑 OWNER: {OWNER_ID}", flush=True)
    print("🔒 Locks: link, id, spam, profanity, kodhengi", flush=True)
    print("⭐ Features: Special users, Welcome", flush=True)
    print("🎮 Games: Connect4 + Coin Flip", flush=True)
    print(f"⚙️  Bot Active: {bot_is_active}", flush=True)
    try: asyncio.create_task(cleanup_task())
    except: pass
    try:
        await bot.run()
    except Exception as e:
        print(f"❌ RUN: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
