import os
import json
import time
import asyncio
import threading
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import websockets

# ==================== CONFIGURATION ====================
BOT_TOKEN = "8482738540:AAH6HKd6704SPAn1q2ERWM8HGV0KxUpeyPk"
ADMIN_ID = 1431950109

SUPABASE_URL = "https://llgkcdygplsqoiomqnah.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxsZ2tjZHlncGxzcW9pb21xbmFoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTM0MjE0NjEsImV4cCI6MjEwODk5NzQ2MX0.OwwM66Su3PFH6b79SOKKsSMonUnH_lghK5mQYZcwHjU"

KEYS_FILE = "keys_db.json"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

# Global states
ONLINE_DEVICES = {}  # dev_code: {"name": str, "room": str, "last_seen": timestamp}
ACTIVE_KEYS = {}     # key_str: {"dev_code": str, "expires": timestamp}
USER_SESSIONS = {}   # tg_user_id: key_str

# ==================== PERSISTENT STORAGE ====================
def load_keys():
    global ACTIVE_KEYS
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, "r") as f:
                ACTIVE_KEYS = json.load(f)
        except Exception:
            ACTIVE_KEYS = {}

def save_keys():
    try:
        with open(KEYS_FILE, "w") as f:
            json.dump(ACTIVE_KEYS, f)
    except Exception:
        pass

# ==================== SUPABASE BACKGROUND TASKS ====================
async def supabase_lobby_listener():
    ws_url = SUPABASE_URL.replace("https://", "wss://") + f"/realtime/v1/websocket?apikey={SUPABASE_KEY}&vsn=1.0.0"
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                join_msg = {
                    "topic": "realtime:lobby",
                    "event": "phx_join",
                    "payload": {"config": {"broadcast": {"acknowledge_broadcasts": False}}},
                    "ref": "1"
                }
                await ws.send(json.dumps(join_msg))
                
                async for message in ws:
                    try:
                        data = json.loads(message)
                        if data.get("event") == "broadcast":
                            payload = data.get("payload", {})
                            inner_event = payload.get("event")
                            inner_payload = payload.get("payload", {})
                            msg = inner_payload.get("msg", "")

                            if inner_event == "host" and msg.startswith("HOST|"):
                                parts = msg.split("|")
                                if len(parts) >= 4:
                                    dev_name = parts[1]
                                    room_id = parts[2]
                                    dev_code = parts[3].strip()
                                    ONLINE_DEVICES[dev_code] = {
                                        "name": dev_name,
                                        "room": room_id,
                                        "last_seen": time.time()
                                    }
                            elif inner_event == "bye":
                                parts = msg.split("|")
                                if len(parts) >= 2:
                                    target = parts[1].strip()
                                    to_del = [k for k, v in ONLINE_DEVICES.items() if v["name"] == target or k == target]
                                    for k in to_del:
                                        ONLINE_DEVICES.pop(k, None)
                    except Exception:
                        pass
        except Exception:
            await asyncio.sleep(3)

async def device_pruner():
    while True:
        now = time.time()
        stale = [k for k, v in ONLINE_DEVICES.items() if now - v["last_seen"] > 15]
        for k in stale:
            ONLINE_DEVICES.pop(k, None)
        await asyncio.sleep(5)

async def send_command_to_room(room_id: str, command: str):
    ws_url = SUPABASE_URL.replace("https://", "wss://") + f"/realtime/v1/websocket?apikey={SUPABASE_KEY}&vsn=1.0.0"
    try:
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({
                "topic": f"realtime:{room_id}",
                "event": "phx_join",
                "payload": {"config": {"broadcast": {"acknowledge_broadcasts": False}}},
                "ref": "1"
            }))
            await asyncio.sleep(0.3)
            await ws.send(json.dumps({
                "topic": f"realtime:{room_id}",
                "event": "broadcast",
                "payload": {
                    "type": "broadcast",
                    "event": "dice",
                    "payload": {"msg": command}
                },
                "ref": "2"
            }))
            await asyncio.sleep(0.2)
    except Exception as e:
        print(f"[Supabase] Send Error: {e}")

def run_async_loop(loop):
    asyncio.set_event_loop(loop)
    loop.create_task(supabase_lobby_listener())
    loop.create_task(device_pruner())
    loop.run_forever()

# Background async thread start
async_loop = asyncio.new_event_loop()
t = threading.Thread(target=run_async_loop, args=(async_loop,), daemon=True)
t.start()

# ==================== BOT HANDLERS ====================
def build_dashboard(key):
    dev_code = ACTIVE_KEYS[key]["dev_code"]
    expires = ACTIVE_KEYS[key]["expires"]
    rem_min = max(0, int((expires - time.time()) // 60))

    is_online = dev_code in ONLINE_DEVICES
    dev_info = ONLINE_DEVICES.get(dev_code, {})
    dev_name = dev_info.get("name", "Unknown Phone")
    status_icon = "🟢 ONLINE" if is_online else "🔴 OFFLINE"

    text = (
        f"📱 *Device:* `{dev_name}`\n"
        f"🔑 *Code:* `{dev_code}`\n"
        f"📡 *Status:* {status_icon}\n"
        f"⏳ *Validity Left:* {rem_min} minutes\n\n"
        f"👑 *Select Guaranteed Winner:*"
    )

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🔴 RED", callback_data="WIN|red"),
        InlineKeyboardButton("🟡 YELLOW", callback_data="WIN|yellow")
    )
    markup.row(
        InlineKeyboardButton("🔵 BLUE", callback_data="WIN|blue"),
        InlineKeyboardButton("🟢 GREEN", callback_data="WIN|green")
    )
    markup.row(InlineKeyboardButton("❌ WINNER OFF", callback_data="WIN|off"))
    markup.row(InlineKeyboardButton("🔄 REFRESH STATUS", callback_data="REFRESH"))
    return text, markup

@bot.message_handler(commands=['start'])
def handle_start(message):
    user_id = message.from_user.id
    if user_id in USER_SESSIONS:
        key = USER_SESSIONS[user_id]
        if key in ACTIVE_KEYS and ACTIVE_KEYS[key]["expires"] > time.time():
            txt, markup = build_dashboard(key)
            bot.send_message(message.chat.id, txt, reply_markup=markup)
            return

    bot.send_message(
        message.chat.id,
        "👋 *LUDO CONTROL BOT*\n\nKripya apna *Access Key* yahan paste karein:"
    )

@bot.message_handler(commands=['admin'])
def handle_admin(message):
    if message.from_user.id != ADMIN_ID:
        return

    now = time.time()
    if not ONLINE_DEVICES:
        bot.send_message(message.chat.id, "📱 *Koi bhi device online nahi hai.*")
        return

    msg = "👑 *LIVE DETECTED DEVICES:*\n\n"
    for code, info in ONLINE_DEVICES.items():
        sec_ago = int(now - info["last_seen"])
        msg += f"• *{info['name']}*\n  Code: `{code}`\n  Room: `{info['room']}`\n  Active: {sec_ago}s pehle\n\n"

    msg += "👉 Key generate karne ke liye:\n`/gen DEV-CODE 2h` (ya 30m, 1d)"
    bot.send_message(message.chat.id, msg)

@bot.message_handler(commands=['gen'])
def handle_gen(message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.strip().split()
    if len(parts) < 3:
        bot.send_message(message.chat.id, "Format: `/gen DEV-CODE 2h`\nExamples: `30m`, `2h`, `1d`")
        return

    dev_code = parts[1].strip()
    dur_str = parts[2].strip().lower()

    mult = 3600
    if dur_str.endswith("m"):
        mult = 60
    elif dur_str.endswith("d"):
        mult = 86400

    try:
        val = int(dur_str[:-1])
        duration_sec = val * mult
    except ValueError:
        duration_sec = 7200

    key_str = f"LUDO-{dur_str.upper()}-{dev_code}"
    ACTIVE_KEYS[key_str] = {
        "dev_code": dev_code,
        "expires": time.time() + duration_sec
    }
    save_keys()

    bot.send_message(
        message.chat.id,
        f"✅ *Key Created!*\n\n"
        f"🔑 Key: `{key_str}`\n"
        f"📱 Device Code: `{dev_code}`\n"
        f"⏳ Validity: {dur_str}\n\n"
        f"Yeh key user ko bhej do."
    )

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    if user_id not in USER_SESSIONS:
        bot.answer_callback_query(call.id, "Session expired! /start dabayein.", show_alert=True)
        return

    key = USER_SESSIONS[user_id]
    if key not in ACTIVE_KEYS or ACTIVE_KEYS[key]["expires"] < time.time():
        bot.answer_callback_query(call.id, "Key expire ho chuki hai!", show_alert=True)
        return

    dev_code = ACTIVE_KEYS[key]["dev_code"]
    data = call.data

    if data == "REFRESH":
        txt, markup = build_dashboard(key)
        try:
            bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=markup)
        except Exception:
            pass
        bot.answer_callback_query(call.id, "Refreshed!")
        return

    if data.startswith("WIN|"):
        color = data.split("|")[1]
        if dev_code not in ONLINE_DEVICES:
            bot.answer_callback_query(call.id, "⚠️ Device OFFLINE hai! Pehle phone me game kholiye.", show_alert=True)
            return

        room_id = ONLINE_DEVICES[dev_code]["room"]
        command = f"PROTECT|{color}"
        asyncio.run_coroutine_threadsafe(send_command_to_room(room_id, command), async_loop)

        bot.answer_callback_query(call.id, f"👑 Set Target Winner: {color.upper()}", show_alert=True)
        txt, markup = build_dashboard(key)
        try:
            bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=markup)
        except Exception:
            pass

@bot.message_handler(func=lambda msg: True)
def handle_text(message):
    user_id = message.from_user.id
    text = message.text.strip()

    if text in ACTIVE_KEYS:
        key_data = ACTIVE_KEYS[text]
        if key_data["expires"] < time.time():
            bot.send_message(message.chat.id, "❌ Yeh Key expire ho chuki hai! Admin se naya key lein.")
            return

        USER_SESSIONS[user_id] = text
        bot.send_message(message.chat.id, "✅ Key Verified!")
        txt, markup = build_dashboard(text)
        bot.send_message(message.chat.id, txt, reply_markup=markup)
    else:
        bot.send_message(message.chat.id, "❌ Invalid Key! Sahi key enter karein.")

# ==================== ENTRY POINT ====================
if __name__ == "__main__":
    load_keys()
    print("🤖 Bot Online...")
    bot.infinity_polling()
