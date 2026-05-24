main.py — Scalping Bot v5.0
python
·
532 lines





"""
SCALPING BOT v5.0 — CoinGecko Edition
Exchange  : No Exchange needed!
Price     : CoinGecko Free API (Real ETH price)
Mode      : Paper Trading
Strategy  : Smart Money (Structure + FVG + Liquidity)
Telegram  : Trade alerts
Target    : 5-10 USDT/day
Capital   : 105 USDT
"""

import time
import os
import json
import requests
import threading
from flask import Flask
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────
#  ENV LOAD
# ─────────────────────────────────────────────
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

if not all([BOT_TOKEN, CHAT_ID]):
    print("[WARN] .env mein Telegram keys missing hain!")

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
CAPITAL          = 105.0
CAPITAL_USE_PCT  = 90
LEVERAGE         = 10
MIN_SCORE        = 6
MAX_DAILY_LOSS   = 5.0
COOLDOWN_SEC     = 120
MAX_HOLD_SEC     = 300
TP_PCT           = 0.8
SL_PCT           = 0.4
SCAN_INTERVAL    = 60
UPDATE_INTERVAL  = 1800

TRADE_HISTORY = "trade_history.json"
CAPITAL_FILE  = "capital.json"

# ─────────────────────────────────────────────
#  FLASK SERVER
# ─────────────────────────────────────────────
app = Flask(__name__)

@app.route('/')
def home():
    capital = load_capital()
    return f"Scalping Bot v5.0 Running! Capital: {capital:.2f} USDT"

def run_server():
    app.run(host='0.0.0.0', port=10000)

# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for attempt in range(3):
        try:
            r = requests.post(
                url,
                data={"chat_id": CHAT_ID, "text": f"🤖 {message}"},
                timeout=15,
            )
            if r.status_code == 200:
                return True
        except Exception as e:
            print(f"[TELEGRAM] Error: {e}")
            time.sleep(3)
    return False

# ─────────────────────────────────────────────
#  COINGECKO — REAL ETH PRICE
# ─────────────────────────────────────────────
def get_eth_price():
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "ethereum",
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        }
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        price      = float(data["ethereum"]["usd"])
        change_24h = float(data["ethereum"]["usd_24h_change"])
        return price, change_24h
    except Exception as e:
        print(f"[PRICE ERROR] {e}")
        return None, None


def get_eth_ohlc():
    try:
        url = "https://api.coingecko.com/api/v3/coins/ethereum/ohlc"
        params = {"vs_currency": "usd", "days": "1"}
        r = requests.get(url, params=params, timeout=15)
        return r.json()
    except Exception as e:
        print(f"[OHLC ERROR] {e}")
        return None


# ─────────────────────────────────────────────
#  SIGNAL ANALYSIS
# ─────────────────────────────────────────────
def analyze_market(price, change_24h, ohlc_data):
    score     = 0
    direction = None
    reasons   = []

    # Rule 1 — 24h Trend
    if change_24h < -2.0:
        score += 2
        direction = "SELL"
        reasons.append(f"24h BEAR trend {change_24h:.1f}% (+2)")
    elif change_24h > 2.0:
        score += 2
        direction = "BUY"
        reasons.append(f"24h BULL trend {change_24h:.1f}% (+2)")
    elif change_24h < -0.5:
        score += 1
        direction = "SELL"
        reasons.append(f"24h slight BEAR {change_24h:.1f}% (+1)")
    elif change_24h > 0.5:
        score += 1
        direction = "BUY"
        reasons.append(f"24h slight BULL {change_24h:.1f}% (+1)")
    else:
        reasons.append(f"24h RANGE {change_24h:.1f}% (0)")

    # Rule 2 — OHLC Analysis
    if ohlc_data and len(ohlc_data) >= 4:
        recent = ohlc_data[-4:]
        closes = [c[4] for c in recent]

        bull_candles = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
        bear_candles = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i-1])

        if bull_candles >= 3 and (direction == "BUY" or direction is None):
            score += 2
            direction = "BUY"
            reasons.append(f"3+ bull candles (+2)")
        elif bear_candles >= 3 and (direction == "SELL" or direction is None):
            score += 2
            direction = "SELL"
            reasons.append(f"3+ bear candles (+2)")

        if len(closes) >= 2:
            momentum = ((closes[-1] - closes[-2]) / closes[-2]) * 100
            if momentum > 0.3 and direction == "BUY":
                score += 1
                reasons.append(f"Bull momentum {momentum:.2f}% (+1)")
            elif momentum < -0.3 and direction == "SELL":
                score += 1
                reasons.append(f"Bear momentum {momentum:.2f}% (+1)")

        highs = [c[2] for c in recent]
        lows  = [c[3] for c in recent]
        if len(highs) >= 2:
            if highs[-1] > highs[-2] and lows[-1] > lows[-2] and direction == "BUY":
                score += 1
                reasons.append("Higher High + Higher Low (+1)")
            elif highs[-1] < highs[-2] and lows[-1] < lows[-2] and direction == "SELL":
                score += 1
                reasons.append("Lower High + Lower Low (+1)")

    # Rule 3 — Volatility check
    if ohlc_data and len(ohlc_data) >= 2:
        last_candle = ohlc_data[-1]
        candle_size = abs(last_candle[2] - last_candle[3])
        candle_pct  = (candle_size / price) * 100
        if candle_pct < 0.05:
            score = max(0, score - 1)
            reasons.append(f"Market too flat (-1)")
        elif candle_pct > 3.0:
            score = max(0, score - 1)
            reasons.append(f"Market too volatile (-1)")

    if direction is None:
        return 0, "WAIT", reasons

    reasons.append(f"Total Score: {score}/8")
    return score, direction, reasons


# ─────────────────────────────────────────────
#  CAPITAL MANAGEMENT
# ─────────────────────────────────────────────
def load_capital():
    try:
        with open(CAPITAL_FILE, "r") as f:
            data = json.load(f)
            return float(data["capital"])
    except Exception:
        return CAPITAL

def save_capital(capital):
    try:
        with open(CAPITAL_FILE, "w") as f:
            json.dump({
                "capital":   round(capital, 4),
                "timestamp": datetime.now().isoformat(),
            }, f)
    except Exception as e:
        print(f"[CAPITAL ERROR] {e}")


# ─────────────────────────────────────────────
#  TRADE HISTORY
# ─────────────────────────────────────────────
def save_trade(side, entry, exit_price, pnl, capital, duration, label):
    try:
        try:
            with open(TRADE_HISTORY, "r") as f:
                history = json.load(f)
        except Exception:
            history = []
        history.append({
            "date":     datetime.now().strftime("%d/%m/%Y"),
            "time":     datetime.now().strftime("%H:%M:%S"),
            "side":     side,
            "entry":    round(entry, 2),
            "exit":     round(exit_price, 2),
            "pnl":      round(pnl, 4),
            "capital":  round(capital, 4),
            "duration": duration,
            "result":   "WIN" if pnl > 0 else "LOSS",
            "label":    label,
        })
        with open(TRADE_HISTORY, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"[HISTORY ERROR] {e}")


def get_daily_pnl():
    try:
        with open(TRADE_HISTORY, "r") as f:
            history = json.load(f)
        today  = datetime.now().strftime("%d/%m/%Y")
        trades = [t for t in history if t["date"] == today]
        return sum(t["pnl"] for t in trades)
    except Exception:
        return 0.0


def get_daily_stats():
    try:
        with open(TRADE_HISTORY, "r") as f:
            history = json.load(f)
    except Exception:
        return None
    today  = datetime.now().strftime("%d/%m/%Y")
    trades = [t for t in history if t["date"] == today]
    if not trades:
        return None
    total    = len(trades)
    wins     = len([t for t in trades if t["result"] == "WIN"])
    losses   = total - wins
    win_rate = round((wins / total) * 100, 1)
    pnl      = round(sum(t["pnl"] for t in trades), 4)
    return {
        "total": total, "wins": wins, "losses": losses,
        "win_rate": win_rate, "pnl": pnl,
        "best":    round(max(t["pnl"] for t in trades), 4),
        "worst":   round(min(t["pnl"] for t in trades), 4),
        "capital": trades[-1]["capital"],
    }


# ─────────────────────────────────────────────
#  DAILY REPORT
# ─────────────────────────────────────────────
def run_daily_report():
    last_date = None
    while True:
        try:
            ist = timezone(timedelta(hours=5, minutes=30))
            now = datetime.now(ist)
            if now.hour == 23 and now.minute == 59:
                if last_date != now.date():
                    last_date = now.date()
                    stats = get_daily_stats()
                    if stats:
                        send_telegram(
                            f"📊 DAILY REPORT — {now.strftime('%d/%m/%Y')}\n"
                            f"────────────────────\n"
                            f"Trades   : {stats['total']}\n"
                            f"Wins     : {stats['wins']} ✅\n"
                            f"Losses   : {stats['losses']} ❌\n"
                            f"Win Rate : {stats['win_rate']}%\n"
                            f"Daily PnL: {stats['pnl']:+.4f} USDT\n"
                            f"Capital  : {stats['capital']:.4f} USDT\n"
                            f"────────────────────"
                        )
                    else:
                        send_telegram("📊 DAILY REPORT\nAaj koi trade nahi hua.")
        except Exception as e:
            print(f"[DAILY REPORT ERROR] {e}")
        time.sleep(30)


# ─────────────────────────────────────────────
#  MAIN BOT
# ─────────────────────────────────────────────
def run_bot():
    capital      = load_capital()
    initial_cap  = capital
    position     = None
    entry_price  = 0.0
    entry_time   = None
    pos_size     = 0.0
    sl_price     = 0.0
    tp_price     = 0.0
    cooldown_end = None
    last_update  = time.time()

    print("[BOT] Scalping Bot v5.0 started!")
    send_telegram(
        f"🚀 SCALPING BOT v5.0 STARTED\n"
        f"────────────────────\n"
        f"Capital  : {capital:.2f} USDT\n"
        f"Symbol   : ETH/USDT\n"
        f"Mode     : Paper Trading\n"
        f"Price    : CoinGecko (Free)\n"
        f"Target   : 5-10 USDT/day\n"
        f"TP       : {TP_PCT}%\n"
        f"SL       : {SL_PCT}%\n"
        f"Leverage : {LEVERAGE}x\n"
        f"Min Score: {MIN_SCORE}/8\n"
        f"────────────────────"
    )

    while True:
        try:
            now_str = datetime.now().strftime("%H:%M:%S")

            # Daily Loss Check
            daily_pnl = get_daily_pnl()
            if daily_pnl < 0:
                loss_pct = (abs(daily_pnl) / initial_cap) * 100
                if loss_pct >= MAX_DAILY_LOSS:
                    send_telegram(
                        f"⛔ DAILY LOSS LIMIT HIT\n"
                        f"Loss : {daily_pnl:.4f} USDT ({loss_pct:.2f}%)\n"
                        f"Bot paused for 1 hour."
                    )
                    time.sleep(3600)
                    initial_cap = capital
                    continue

            # Price Fetch
            price, change_24h = get_eth_price()
            if price is None:
                time.sleep(30)
                continue

            # Periodic Update
            if time.time() - last_update >= UPDATE_INTERVAL:
                last_update = time.time()
                if position is not None and entry_time is not None:
                    pnl = (price - entry_price) * pos_size if position == "BUY" \
                          else (entry_price - price) * pos_size
                    dur = str(datetime.now() - entry_time).split(".")[0]
                    send_telegram(
                        f"📍 UPDATE\n"
                        f"Side    : {position}\n"
                        f"Entry   : {entry_price:.2f}\n"
                        f"Price   : {price:.2f}\n"
                        f"PnL     : {pnl:+.4f} USDT\n"
                        f"Capital : {capital:.4f} USDT\n"
                        f"Time    : {dur}"
                    )
                else:
                    send_telegram(
                        f"💤 STATUS\n"
                        f"ETH     : ${price:.2f}\n"
                        f"24h     : {change_24h:+.2f}%\n"
                        f"Capital : {capital:.4f} USDT\n"
                        f"Status  : Waiting for signal..."
                    )

            # Max Hold Check
            if position is not None and entry_time is not None:
                held = (datetime.now() - entry_time).seconds
                if held >= MAX_HOLD_SEC:
                    pnl = (price - entry_price) * pos_size if position == "BUY" \
                          else (entry_price - price) * pos_size
                    capital += pnl
                    duration = str(datetime.now() - entry_time).split(".")[0]
                    save_capital(capital)
                    save_trade(position, entry_price, price, pnl,
                               capital, duration, "Max Hold")
                    send_telegram(
                        f"⏰ CLOSED — Max Hold\n"
                        f"Side    : {position}\n"
                        f"Entry   : {entry_price:.2f}\n"
                        f"Exit    : {price:.2f}\n"
                        f"PnL     : {pnl:+.4f} USDT\n"
                        f"Capital : {capital:.4f} USDT"
                    )
                    position = None; entry_price = 0.0
                    entry_time = None; pos_size = 0.0
                    cooldown_end = time.time() + COOLDOWN_SEC
                    time.sleep(SCAN_INTERVAL)
                    continue

            # SL / TP Check
            if position is not None:
                hit_sl = (position == "BUY"  and price <= sl_price) or \
                         (position == "SELL" and price >= sl_price)
                hit_tp = (position == "BUY"  and price >= tp_price) or \
                         (position == "SELL" and price <= tp_price)

                if hit_sl or hit_tp:
                    label = "✅ TAKE PROFIT" if hit_tp else "❌ STOP LOSS"
                    pnl   = (price - entry_price) * pos_size if position == "BUY" \
                            else (entry_price - price) * pos_size
                    capital += pnl
                    duration = str(datetime.now() - entry_time).split(".")[0]
                    save_capital(capital)
                    save_trade(position, entry_price, price, pnl,
                               capital, duration, label)
                    send_telegram(
                        f"{label}\n"
                        f"────────────────────\n"
                        f"Side    : {position}\n"
                        f"Entry   : ${entry_price:.2f}\n"
                        f"Exit    : ${price:.2f}\n"
                        f"PnL     : {pnl:+.4f} USDT\n"
                        f"Capital : {capital:.4f} USDT\n"
                        f"Time    : {duration}\n"
                        f"────────────────────"
                    )
                    position = None; entry_price = 0.0
                    entry_time = None; pos_size = 0.0
                    cooldown_end = time.time() + COOLDOWN_SEC
                    time.sleep(SCAN_INTERVAL)
                    continue

            # Cooldown Check
            if cooldown_end and time.time() < cooldown_end:
                remaining = int(cooldown_end - time.time())
                print(f"[{now_str}] Cooldown {remaining}s | ETH=${price:.2f}")
                time.sleep(SCAN_INTERVAL)
                continue

            # Signal Analysis
            if position is None:
                ohlc = get_eth_ohlc()
                time.sleep(2)
                score, signal, reasons = analyze_market(price, change_24h, ohlc)
                reason_str = " | ".join(reasons)
                print(f"[{now_str}] Score={score}/8 | {signal} | ETH=${price:.2f}")

                if score >= MIN_SCORE and signal in ["BUY", "SELL"]:
                    capital_used = capital * (CAPITAL_USE_PCT / 100)
                    pos_size     = (capital_used * LEVERAGE) / price
                    entry_price  = price
                    entry_time   = datetime.now()
                    position     = signal

                    if signal == "BUY":
                        sl_price = entry_price * (1 - SL_PCT / 100)
                        tp_price = entry_price * (1 + TP_PCT / 100)
                    else:
                        sl_price = entry_price * (1 + SL_PCT / 100)
                        tp_price = entry_price * (1 - TP_PCT / 100)

                    send_telegram(
                        f"📈 TRADE OPENED\n"
                        f"────────────────────\n"
                        f"Side     : {position}\n"
                        f"Entry    : ${entry_price:.2f}\n"
                        f"SL       : ${sl_price:.2f}\n"
                        f"TP       : ${tp_price:.2f}\n"
                        f"Capital  : ${capital_used:.2f} USDT\n"
                        f"Score    : {score}/8\n"
                        f"Reason   : {reason_str[:200]}\n"
                        f"────────────────────"
                    )
                else:
                    print(f"[{now_str}] WAIT | Score={score}/8 | ETH=${price:.2f}")
            else:
                pnl_now = (price - entry_price) * pos_size if position == "BUY" \
                          else (entry_price - price) * pos_size
                print(f"[{now_str}] {position} | PnL={pnl_now:+.4f} | ETH=${price:.2f}")

        except Exception as e:
            print(f"[BOT ERROR] {e}")
            time.sleep(30)

        time.sleep(SCAN_INTERVAL)


# ─────────────────────────────────────────────
#  START
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  SCALPING BOT v5.0 — CoinGecko Edition")
    print("  No Exchange | No API Key | No Ban")
    print("  Paper Trading | ETH/USDT")
    print("=" * 50)

    t1 = threading.Thread(target=run_server,       daemon=True)
    t2 = threading.Thread(target=run_bot,          daemon=True)
    t3 = threading.Thread(target=run_daily_report, daemon=True)

    for t in [t1, t2, t3]:
        t.start()

    print("[INFO] Flask   : port 10000")
    print("[INFO] Bot     : started")
    print("[INFO] Report  : 23:59 IST")
    print("[INFO] 24/7    : ON")

    while True:
        time.sleep(60)
