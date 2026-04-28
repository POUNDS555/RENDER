import os, json, re, time, random, asyncio, threading, logging
from datetime import datetime, date, timedelta
from flask import Flask
from telethon import TelegramClient, events
import openai
import schedule

# -------------------- CONFIG FROM ENV --------------------
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
PHONE = os.environ["PHONE"]
OPENAI_KEY = os.environ["OPENAI_KEY"]
openai.api_key = OPENAI_KEY

# -------------------- TRIVIA GROUPS --------------------
DEFAULT_GROUPS = """[
  {
    "name": "BNB Trivia Bot",
    "group_id": "@BNBTriviaBot",
    "token": "BNB",
    "min_withdraw": 0.01,
    "wallet": "0x11B67115cb9142DFBBaF59f96E009a6F5851C48C"
  },
  {
    "name": "TRX Quiz",
    "group_id": "@TRXQuizBot",
    "token": "TRX",
    "min_withdraw": 10,
    "wallet": "TEm2JNmcNSq4oqWbvLHn98B5ei4q6PXc7U"
  },
  {
    "name": "TON Game",
    "group_id": "@TONGameBot",
    "token": "TON",
    "min_withdraw": 0.5,
    "wallet": "UQDzpBc7ifezHnx8f1LD87JGhoYkFLsVxQtmjvbv4jQy9P5g"
  }
]"""
TRIVIA_GROUPS = json.loads(os.environ.get("TRIVIA_GROUPS", DEFAULT_GROUPS))
target_chats = [g["group_id"] for g in TRIVIA_GROUPS]

# -------------------- LOGGING --------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# -------------------- FLASK --------------------
app = Flask(__name__)
@app.route("/")
def home():
    return "Ultra-Premium Money Printer is alive!", 200

# -------------------- TELEGRAM CLIENT --------------------
client = TelegramClient("ultra_session", API_ID, API_HASH)

# -------------------- EARNINGS DATA --------------------
EARNINGS_FILE = "ultra_earnings.json"

def load_earnings():
    if not os.path.exists(EARNINGS_FILE):
        return {}
    with open(EARNINGS_FILE) as f:
        return json.load(f)

def save_earnings(data):
    with open(EARNINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def log_win(amount, token, source=""):
    data = load_earnings()
    today = str(date.today())
    data.setdefault(today, [])
    data[today].append({
        "time": datetime.now().strftime("%H:%M"),
        "amount": amount,
        "token": token,
        "source": source
    })
    save_earnings(data)
    logger.info(f"💰 Logged win: +{amount} {token} from {source}")

def daily_summary():
    data = load_earnings()
    today = str(date.today())
    wins = data.get(today, [])
    if not wins:
        return "No wins logged today."
    total = sum(w["amount"] for w in wins)
    tokens = {}
    for w in wins:
        tokens[w["token"]] = tokens.get(w["token"], 0) + w["amount"]
    det = ", ".join(f"{amt:.3f} {tok}" for tok, amt in tokens.items())
    return f"📅 Today: {total:.3f} total\n{det}"

def threshold_report():
    data = load_earnings()
    today = str(date.today())
    wins = data.get(today, [])
    earned = {}
    for w in wins:
        earned[w["token"]] = earned.get(w["token"], 0) + w["amount"]
    msg = "📊 Withdrawal Status:\n"
    for g in TRIVIA_GROUPS:
        tok = g["token"]
        bal = earned.get(tok, 0)
        need = g["min_withdraw"]
        pct = (bal / need * 100) if need else 0
        msg += f"{g['name']} ({tok}): {bal:.3f}/{need} "
        msg += "✅ READY\n" if bal >= need else f"({pct:.0f}%)\n"
    return msg

# -------------------- AI ANSWER ENGINE --------------------
def ai_answer(question):
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a trivia genius. Answer in 1–5 words. Occasionally make a silly mistake (10%)."},
                {"role": "user", "content": question}
            ],
            temperature=0.6,
            max_tokens=25
        )
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"OpenAI: {e}")
        return None

# -------------------- ANTI-DETECTION --------------------
HUMAN_DELAY = (1.2, 4.5)
WRONG_PROB = 0.08

def human_delay():
    return random.uniform(*HUMAN_DELAY)

def maybe_sabotage():
    if random.random() < WRONG_PROB:
        return random.choice([
            "I think 42", "No idea", "Maybe Paris?", "Probably 2020", "idk lol",
            "Could be false", "Hmm... C?", "Not sure", "Let me guess... Ethiopia"
        ])
    return None

def vary_answer(ans):
    if random.random() < 0.1:
        ans = ans.lower()
    elif random.random() < 0.05:
        ans = ans.upper()
    return ans

# -------------------- WIN DETECTION --------------------
# Patterns that indicate a win (case‑insensitive)
WIN_PATTERNS = [
    r"you won ([\d\.]+)\s*([A-Za-z]{2,10})",
    r"congratulations.*?won ([\d\.]+)\s*([A-Za-z]{2,10})",
    r"🎉.*?won ([\d\.]+)\s*([A-Za-z]{2,10})",
    r"reward:?\s*([\d\.]+)\s*([A-Za-z]{2,10})",
    r"\+([\d\.]+)\s*([A-Za-z]{2,10})\s*(?:token|coin)",
]

OWN_USERNAME = None   # set after login

async def detect_and_log_win(message):
    global OWN_USERNAME
    text = message.text
    if not text:
        return
    # Check if message mentions the bot's own username (or first name)
    if OWN_USERNAME and OWN_USERNAME.lower() not in text.lower():
        return
    for pattern in WIN_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            amount = float(m.group(1))
            token = m.group(2).upper()
            # Validate token is one of our tracked tokens
            known_tokens = {g["token"].upper() for g in TRIVIA_GROUPS}
            if token in known_tokens:
                group_name = ""
                # find group name from chat
                for g in TRIVIA_GROUPS:
                    if g["group_id"] == str(message.chat_id) or g["group_id"] == message.chat.username:
                        group_name = g["name"]
                        break
                log_win(amount, token, source=group_name)
                # Send DM to Saved Messages
                try:
                    await client.send_message("me", f"🏆 Auto‑win: +{amount} {token} in {group_name}")
                except:
                    pass
                return  # log once per message

# -------------------- TELEGRAM HANDLERS --------------------
@client.on(events.NewMessage(incoming=True))
async def handle_all(event):
    """Route messages: if it's a question in a target group → answer.
       If it's a win announcement → log it."""
    if event.is_private:
        return  # we only care about group messages
    try:
        chat = await event.get_chat()
    except:
        return
    if chat.username not in target_chats and str(chat.id) not in target_chats:
        return

    text = event.message.text or ""
    # WIN DETECTION (check first, before answering, because answer might trigger ban if we answer our own win)
    await detect_and_log_win(event.message)

    # QUESTION ANSWERING
    if "?" in text and len(text) < 300:
        logger.info(f"Question in {chat.username or chat.id}: {text[:80]}")
        # random human delay
        delay = human_delay()
        await asyncio.sleep(delay)
        # maybe sabotage
        wrong = maybe_sabotage()
        if wrong:
            answer = wrong
            logger.info("Deliberate wrong answer")
        else:
            answer = ai_answer(text)
            if not answer:
                return
            answer = vary_answer(answer)
        try:
            await event.reply(answer)
            logger.info(f"Answered: {answer}")
        except Exception as e:
            logger.error(f"Reply error: {e}")

# -------------------- COMMANDS (private chat or Saved Messages) --------------------
@client.on(events.NewMessage(pattern=r"^/status$"))
async def cmd_status(event):
    await event.reply(f"✅ Ultra‑Premium Bot online.\n{daily_summary()}")

@client.on(events.NewMessage(pattern=r"^/earnings$"))
async def cmd_earnings(event):
    await event.reply(daily_summary())

@client.on(events.NewMessage(pattern=r"^/balance$"))
async def cmd_balance(event):
    await event.reply(threshold_report())

@client.on(events.NewMessage(pattern=r"^/wallet$"))
async def cmd_wallet(event):
    msg = "🔑 Linked wallets:\n"
    for g in TRIVIA_GROUPS:
        msg += f"{g['token']}: {g['wallet'][:15]}... (min {g['min_withdraw']})\n"
    await event.reply(msg)

@client.on(events.NewMessage(pattern=r"^/log ([\d\.]+) (\w+)$"))
async def cmd_log(event):
    amount = float(event.pattern_match.group(1))
    token = event.pattern_match.group(2)
    log_win(amount, token, "manual")
    await event.reply(f"✅ Manual log: +{amount} {token}")

# -------------------- DAILY REPORT --------------------
async def send_daily_report():
    """Send a DM every day at 9:00 UTC with earnings and threshold status."""
    msg = f"☀️ Daily Report\n{daily_summary()}\n\n{threshold_report()}"
    try:
        await client.send_message("me", msg)
    except:
        pass

def run_scheduler():
    """Blocking loop to run scheduled tasks."""
    while True:
        schedule.run_pending()
        time.sleep(60)

# -------------------- BOT START --------------------
async def main():
    global OWN_USERNAME
    await client.start(phone=PHONE)
    me = await client.get_me()
    OWN_USERNAME = me.username
    logger.info(f"Bot logged in as @{OWN_USERNAME}")

    # Schedule daily report at 09:00 UTC
    schedule.every().day.at("09:00").do(lambda: asyncio.create_task(send_daily_report()))
    # Start scheduler in a separate thread
    threading.Thread(target=run_scheduler, daemon=True).start()

    logger.info("Ultra‑Premium Money Printer is running...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    # Launch Flask in a daemon thread, then start the bot
    threading.Thread(target=app.run, kwargs={"host":"0.0.0.0", "port":int(os.environ.get("PORT",10000))}, daemon=True).start()
    asyncio.run(main())
