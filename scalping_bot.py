"""
SCALPING BOT v4.0 — Full Upgrade Edition
Strategy  : Smart Money (OB + Liquidity + FVG)
Sessions  : 24/7
Timeframes: 15m (direction) + 5m (setup) + 1m (entry)
Min Score : 7/10
Capital   : 90% per trade
TP Zone   : 70-90% early exit
Max Hold  : 3 min

UPGRADES v4.0:
  [1] WebSocket — live price, no rate limit
  [2] 15m timeframe — better direction signal
  [3] Market condition filter — no bad trades
  [4] TP = Liquidity zone target
  [5] Scan timings fixed (15s execute, 90s decision)
"""

import threading
import time
import os
import json
import requests
import shutil
import websocket
import ccxt
import pandas as pd
import numpy as np
from queue import Queue, Empty
from flask import Flask
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────
#  ENV LOAD
# ─────────────────────────────────────────────
load_dotenv()

API_KEY    = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_SECRET", "")
BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "8161773850:AAFcWw3UnlSe2TrMooB2uvgZQZUqIW0zW2w")
CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "7102976298")

if not all([API_KEY, API_SECRET, BOT_TOKEN, CHAT_ID]):
    print("[WARN] .env mein kuch keys missing hain — check karo!")

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
SYMBOL           = "ETH/USDT:USDT"
WS_SYMBOL        = "ethusdt"          # WebSocket ke liye lowercase

CAPITAL          = 100.0
CAPITAL_USE_PCT  = 90
LEVERAGE         = 10

# [UPGRADE 2] 10-point scoring system
MIN_SCORE        = 7
MIN_CONFIDENCE   = int((MIN_SCORE / 10) * 100)

MAX_DAILY_LOSS_PCT = 5.0

# [UPGRADE 5] Fixed scan timings
EXECUTE_SCAN     = 15    # 8 → 15 sec
DECISION_SCAN    = 90    # 60 → 90 sec
COOLDOWN         = 60
MAX_HOLD_SECONDS = 180

ATR_PERIOD       = 7
ATR_SL_MULT      = 1.0   # tight SL
ATR_TP_MULT      = 1.5

TP_EXIT_MIN_PCT   = 0.70
TP_EXIT_MAX_PCT   = 0.90
TP_HOLD_MIN_SCORE = 8

UPDATE_INTERVAL  = 1800

signal_queue = Queue(maxsize=1)

LOG_FILE       = "scalping_log.json"
CAPITAL_FILE   = "scalping_capital.txt"
TRADE_HISTORY  = "scalping_history.json"

state_lock   = threading.Lock()
error_counts = {}

# [UPGRADE 1] WebSocket live price storage
ws_price_lock = threading.Lock()
ws_last_price = {"price": 0.0, "time": 0}


# ─────────────────────────────────────────────
#  FLASK SERVER
# ─────────────────────────────────────────────
app = Flask(__name__)

@app.route('/')
def home():
    return "Scalping Bot v4.0 Upgrade Running!"

def run_server():
    app.run(host='0.0.0.0', port=8081)


# ─────────────────────────────────────────────
#  ERROR TRACKING
# ─────────────────────────────────────────────
def track_error(source: str, e: Exception):
    error_counts[source] = error_counts.get(source, 0) + 1
    count = error_counts[source]
    print(f"[{source} ERROR] ({count}) {type(e).__name__}: {e}")
    if count == 10:
        send_telegram(f"WARNING: {source} mein 10+ errors!\n{type(e).__name__}: {e}")


# ─────────────────────────────────────────────
#  [UPGRADE 1] WEBSOCKET — LIVE PRICE
# ─────────────────────────────────────────────
def on_ws_message(ws, message):
    global ws_last_price
    try:
        data = json.loads(message)
        price = float(data.get("p") or data.get("c") or 0)
        if price > 0:
            with ws_price_lock:
                ws_last_price["price"] = price
                ws_last_price["time"]  = time.time()
    except Exception as e:
        pass

def on_ws_error(ws, error):
    print(f"[WS ERROR] {error}")

def on_ws_close(ws, close_status_code, close_msg):
    print("[WS] Connection closed — reconnecting in 5s...")
    time.sleep(5)

def on_ws_open(ws):
    print("[WS] Connected — live price stream ON!")

def run_websocket():
    """
    Binance WebSocket se live ETH/USDT price lao
    Koi REST request nahi = koi rate limit nahi
    """
    while True:
        try:
            url = f"wss://fstream.binance.com/ws/{WS_SYMBOL}@trade"
            ws = websocket.WebSocketApp(
                url,
                on_message=on_ws_message,
                on_error=on_ws_error,
                on_close=on_ws_close,
                on_open=on_ws_open,
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            print(f"[WS RESTART] {e}")
        time.sleep(5)

def get_ws_price():
    """WebSocket se current price lo"""
    with ws_price_lock:
        price = ws_last_price["price"]
        age   = time.time() - ws_last_price["time"]
    if price > 0 and age < 30:
        return price
    return None


# ─────────────────────────────────────────────
#  CAPITAL
# ─────────────────────────────────────────────
def load_capital():
    try:
        with open(CAPITAL_FILE, "r") as f:
            data = json.load(f)
            cap = float(data["capital"])
            print(f"[CAPITAL] Loaded: {cap} USDT")
            return cap
    except Exception:
        try:
            with open(CAPITAL_FILE, "r") as f:
                cap = float(f.read().strip())
                print(f"[CAPITAL] Loaded (legacy): {cap} USDT")
                return cap
        except Exception:
            print(f"[CAPITAL] Default {CAPITAL} USDT")
            return CAPITAL

def save_capital(capital):
    tmp = CAPITAL_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump({
                "capital":   round(capital, 6),
                "timestamp": datetime.now().isoformat(),
                "backup":    True,
            }, f)
        shutil.move(tmp, CAPITAL_FILE)
    except Exception as e:
        print(f"[CAPITAL SAVE ERROR] {e}")
        try:
            os.remove(tmp)
        except Exception:
            pass


# ─────────────────────────────────────────────
#  DAILY LOSS CHECK
# ─────────────────────────────────────────────
def get_daily_pnl():
    try:
        with open(TRADE_HISTORY, "r", encoding="utf-8") as f:
            history = json.load(f)
        today  = datetime.now().strftime("%d/%m/%Y")
        trades = [t for t in history if t["date"] == today]
        return sum(t["pnl"] for t in trades)
    except Exception:
        return 0.0


# ─────────────────────────────────────────────
#  TRADE HISTORY
# ─────────────────────────────────────────────
def save_trade_history(side, entry, exit_price, pnl,
                       capital, duration, label):
    try:
        try:
            with open(TRADE_HISTORY, "r", encoding="utf-8") as f:
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
        with open(TRADE_HISTORY, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"[HISTORY ERROR] {e}")


def get_daily_stats():
    try:
        with open(TRADE_HISTORY, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        return None
    today  = datetime.now().strftime("%d/%m/%Y")
    trades = [t for t in history if t["date"] == today]
    if not trades:
        return None
    total     = len(trades)
    wins      = len([t for t in trades if t["result"] == "WIN"])
    losses    = total - wins
    win_rate  = round((wins / total) * 100, 1) if total > 0 else 0
    daily_pnl = round(sum(t["pnl"] for t in trades), 4)
    return {
        "total": total, "wins": wins, "losses": losses,
        "win_rate": win_rate, "pnl": daily_pnl,
        "best":  round(max(t["pnl"] for t in trades), 4),
        "worst": round(min(t["pnl"] for t in trades), 4),
        "capital": trades[-1]["capital"],
    }


def get_overall_stats():
    try:
        with open(TRADE_HISTORY, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        return None
    if not history:
        return None
    total     = len(history)
    wins      = len([t for t in history if t["result"] == "WIN"])
    losses    = total - wins
    win_rate  = round((wins / total) * 100, 1) if total > 0 else 0
    total_pnl = round(sum(t["pnl"] for t in history), 4)
    return {
        "total": total, "wins": wins, "losses": losses,
        "win_rate": win_rate, "pnl": total_pnl,
        "best":  round(max(t["pnl"] for t in history), 4),
        "worst": round(min(t["pnl"] for t in history), 4),
        "capital": history[-1]["capital"],
    }


# ─────────────────────────────────────────────
#  EXCHANGE
# ─────────────────────────────────────────────
def get_exchange():
    ex = ccxt.binanceusdm({
        "apiKey":          API_KEY,
        "secret":          API_SECRET,
        "enableRateLimit": True,
        "rateLimit":       200,     # conservative
    })
    ex.load_markets()
    print("[INFO] Binance USDT-M Futures connected")
    return ex


def safe_fetch_ohlcv(ex, symbol, tf, limit, retries=3):
    for i in range(retries):
        try:
            bars = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
            return bars
        except Exception as e:
            if "429" in str(e) or "Too Many" in str(e):
                wait = (i + 1) * 45
                print(f"[RATE LIMIT] {tf} wait {wait}s...")
                time.sleep(wait)
            else:
                print(f"[OHLCV ERROR] {tf}: {e}")
                time.sleep(5)
    return None


# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for attempt in range(3):
        try:
            r = requests.post(
                url,
                data={"chat_id": CHAT_ID, "text": f"[SCALP] {message}"},
                timeout=15,
            )
            if r.status_code == 200:
                return
        except Exception as e:
            print(f"[TELEGRAM] attempt {attempt+1}/3: {e}")
            time.sleep(3)
    print("[TELEGRAM] Message send nahi hua")


# ─────────────────────────────────────────────
#  ATR
# ─────────────────────────────────────────────
def calc_atr(df, period=7):
    try:
        high  = df["high"]
        low   = df["low"]
        close = df["close"]
        tr1   = high - low
        tr2   = (high - close.shift(1)).abs()
        tr3   = (low  - close.shift(1)).abs()
        tr    = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return float(tr.ewm(span=period, adjust=False).mean().iloc[-1])
    except Exception as e:
        track_error("calc_atr", e)
        return 0.0


# ─────────────────────────────────────────────
#  MARKET STRUCTURE
# ─────────────────────────────────────────────
def detect_structure(df, swing_bars=2):
    try:
        highs = df["high"].values
        lows  = df["low"].values
        n     = len(highs)
        swing_highs, swing_lows = [], []
        for i in range(swing_bars, n - swing_bars):
            if highs[i] == max(highs[i - swing_bars: i + swing_bars + 1]):
                swing_highs.append(highs[i])
            if lows[i] == min(lows[i - swing_bars: i + swing_bars + 1]):
                swing_lows.append(lows[i])
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return "RANGE"
        hh = swing_highs[-1] > swing_highs[-2]
        hl = swing_lows[-1]  > swing_lows[-2]
        lh = swing_highs[-1] < swing_highs[-2]
        ll = swing_lows[-1]  < swing_lows[-2]
        if hh and hl:   return "BULL"
        elif lh and ll: return "BEAR"
        return "RANGE"
    except Exception as e:
        track_error("detect_structure", e)
        return "RANGE"


# ─────────────────────────────────────────────
#  [UPGRADE 3] MARKET CONDITION FILTER
# ─────────────────────────────────────────────
def is_good_market(df_1m, atr):
    """
    Sirf tab trade karo jab market condition theek ho:
    1. ATR too low nahi (flat market skip)
    2. ATR too high nahi (extremely volatile skip)
    3. Volume achha ho (dead market skip)
    4. Market ranging nahi ho (trending ho)
    Returns: (bool, reason_string)
    """
    try:
        current_price = float(df_1m["close"].iloc[-1])

        # ATR % of price
        atr_pct = (atr / current_price) * 100 if current_price > 0 else 0

        # Very flat market — ATR < 0.05% of price
        if atr_pct < 0.05:
            return False, f"Market bahut flat hai (ATR={atr_pct:.3f}%)"

        # Extremely volatile — ATR > 2% of price
        if atr_pct > 2.0:
            return False, f"Market bahut volatile hai (ATR={atr_pct:.3f}%)"

        # Volume check
        recent_vol = df_1m["volume"].tail(5).mean()
        avg_vol    = df_1m["volume"].tail(20).mean()
        if avg_vol > 0 and recent_vol < avg_vol * 0.4:
            return False, f"Volume bahut kam hai ({recent_vol:.0f} vs avg {avg_vol:.0f})"

        return True, "Market condition OK"

    except Exception as e:
        track_error("is_good_market", e)
        return True, "Filter error — allow"


# ─────────────────────────────────────────────
#  ORDER BLOCKS
# ─────────────────────────────────────────────
def detect_order_blocks(df, lookback=40):
    try:
        recent        = df.tail(lookback).reset_index(drop=True)
        n             = len(recent)
        current_price = recent["close"].iloc[-1]
        bullish_obs   = []
        bearish_obs   = []

        for i in range(1, n - 1):
            curr  = recent.iloc[i]
            next_ = recent.iloc[i + 1]

            curr_body = abs(curr["close"] - curr["open"])
            next_body = abs(next_["close"] - next_["open"])

            if curr_body == 0:
                continue

            # Bearish OB
            if (curr["close"] > curr["open"] and
                    next_["close"] < next_["open"] and
                    next_body > curr_body * 1.2):
                ob_top    = curr["high"]
                ob_bottom = curr["open"]
                tolerance = (ob_top - ob_bottom) * 0.15
                in_zone   = (ob_bottom - tolerance <= current_price
                             <= ob_top + tolerance)
                candles_after = recent.iloc[i + 2:]
                mitigated = any(
                    (row["close"] >= ob_bottom and row["close"] <= ob_top)
                    for _, row in candles_after.iterrows()
                ) if len(candles_after) > 0 else False
                bearish_obs.append({
                    "top":         round(ob_top, 4),
                    "bottom":      round(ob_bottom, 4),
                    "price_in_ob": in_zone and not mitigated,
                    "fresh":       (i >= n - 10),
                    "mitigated":   mitigated,
                    "strength":    round(next_body / curr_body, 2),
                    "idx":         i,
                })

            # Bullish OB
            if (curr["close"] < curr["open"] and
                    next_["close"] > next_["open"] and
                    next_body > curr_body * 1.2):
                ob_top    = curr["open"]
                ob_bottom = curr["low"]
                tolerance = (ob_top - ob_bottom) * 0.15
                in_zone   = (ob_bottom - tolerance <= current_price
                             <= ob_top + tolerance)
                candles_after = recent.iloc[i + 2:]
                mitigated = any(
                    (row["close"] >= ob_bottom and row["close"] <= ob_top)
                    for _, row in candles_after.iterrows()
                ) if len(candles_after) > 0 else False
                bullish_obs.append({
                    "top":         round(ob_top, 4),
                    "bottom":      round(ob_bottom, 4),
                    "price_in_ob": in_zone and not mitigated,
                    "fresh":       (i >= n - 10),
                    "mitigated":   mitigated,
                    "strength":    round(next_body / curr_body, 2),
                    "idx":         i,
                })

        return {
            "bullish_obs": bullish_obs[-5:],
            "bearish_obs": bearish_obs[-5:],
        }
    except Exception as e:
        track_error("detect_order_blocks", e)
        return {"bullish_obs": [], "bearish_obs": []}


# ─────────────────────────────────────────────
#  LIQUIDITY
# ─────────────────────────────────────────────
def detect_liquidity(df, lookback=40):
    try:
        recent        = df.tail(lookback)
        highs         = recent["high"].values
        lows          = recent["low"].values
        current_price = df["close"].iloc[-1]
        n             = len(highs)
        swing_bars    = 2
        buy_liq       = []
        sell_liq      = []

        for i in range(swing_bars, n - swing_bars):
            if highs[i] == max(highs[i - swing_bars: i + swing_bars + 1]):
                buy_liq.append(highs[i])
            if lows[i] == min(lows[i - swing_bars: i + swing_bars + 1]):
                sell_liq.append(lows[i])

        avg_volume = df["volume"].tail(20).mean()
        buy_swept  = False
        sell_swept = False

        if buy_liq:
            last_high    = buy_liq[-1]
            recent_5     = df.tail(5)
            tolerance    = last_high * 0.002
            sweep_candle = recent_5[recent_5["high"] > last_high - tolerance]
            if not sweep_candle.empty:
                sweep_vol = sweep_candle["volume"].max()
                if (sweep_vol > avg_volume * 1.5 and
                        current_price < last_high * 1.003):
                    buy_swept = True

        if sell_liq:
            last_low     = sell_liq[-1]
            recent_5     = df.tail(5)
            tolerance    = last_low * 0.002
            sweep_candle = recent_5[recent_5["low"] < last_low + tolerance]
            if not sweep_candle.empty:
                sweep_vol = sweep_candle["volume"].max()
                if (sweep_vol > avg_volume * 1.5 and
                        current_price > last_low * 0.997):
                    sell_swept = True

        return {
            "buy_swept":   buy_swept,
            "sell_swept":  sell_swept,
            "buy_levels":  buy_liq[-3:] if buy_liq else [],
            "sell_levels": sell_liq[-3:] if sell_liq else [],
        }
    except Exception as e:
        track_error("detect_liquidity", e)
        return {"buy_swept": False, "sell_swept": False,
                "buy_levels": [], "sell_levels": []}


# ─────────────────────────────────────────────
#  FVG
# ─────────────────────────────────────────────
def detect_fvg(df, lookback=30):
    try:
        fvgs          = []
        recent        = df.tail(lookback).reset_index(drop=True)
        n             = len(recent)
        current_price = recent["close"].iloc[-1]

        for i in range(2, n):
            c1 = recent.iloc[i - 2]
            c3 = recent.iloc[i]

            if c1["high"] < c3["low"]:
                gap_size = ((c3["low"] - c1["high"]) / c1["high"]) * 100
                if gap_size >= 0.02:
                    tolerance = (c3["low"] - c1["high"]) * 0.3
                    fvgs.append({
                        "type":   "BULL",
                        "top":    round(c3["low"], 4),
                        "bottom": round(c1["high"], 4),
                        "size":   round(gap_size, 3),
                        "fresh":  (i >= n - 8),
                        "retest": (c1["high"] - tolerance <= current_price
                                   <= c3["low"] + tolerance),
                    })

            elif c1["low"] > c3["high"]:
                gap_size = ((c1["low"] - c3["high"]) / c3["high"]) * 100
                if gap_size >= 0.02:
                    tolerance = (c1["low"] - c3["high"]) * 0.3
                    fvgs.append({
                        "type":   "BEAR",
                        "top":    round(c1["low"], 4),
                        "bottom": round(c3["high"], 4),
                        "size":   round(gap_size, 3),
                        "fresh":  (i >= n - 8),
                        "retest": (c3["high"] - tolerance <= current_price
                                   <= c1["low"] + tolerance),
                    })

        return fvgs
    except Exception as e:
        track_error("detect_fvg", e)
        return []


# ─────────────────────────────────────────────
#  [UPGRADE 2] SMART MONEY SCORE — 10 POINT
#  15m direction (+2) + 5m setup (+2) + 1m entry (+1)
#  OB (+2) + Liq Sweep (+2) + FVG (+1) = 10 total
# ─────────────────────────────────────────────
def smart_money_score(structure_15m, structure_5m, structure_1m,
                      liq, obs, fvgs):
    points    = 0
    direction = None
    reasons   = []

    # [15m] Direction — 2 points
    if structure_15m == "BULL":
        points += 2
        direction = "BUY"
        reasons.append("15m BULL direction (+2)")
    elif structure_15m == "BEAR":
        points += 2
        direction = "SELL"
        reasons.append("15m BEAR direction (+2)")
    else:
        reasons.append("15m RANGE (0) — weak direction")
        # Fallback to 5m for direction
        if structure_5m == "BULL":
            direction = "BUY"
        elif structure_5m == "BEAR":
            direction = "SELL"

    # [5m] Setup — 2 points
    if direction == "BUY" and structure_5m == "BULL":
        points += 2
        reasons.append("5m BULL setup (+2)")
    elif direction == "SELL" and structure_5m == "BEAR":
        points += 2
        reasons.append("5m BEAR setup (+2)")
    elif structure_5m == "RANGE":
        reasons.append("5m RANGE (0)")
    else:
        reasons.append("5m conflicts direction (0)")

    # [1m] Entry confirm — 1 point
    if direction is not None:
        if (direction == "BUY"  and structure_1m == "BULL") or \
           (direction == "SELL" and structure_1m == "BEAR"):
            points += 1
            reasons.append(f"1m confirms {direction} (+1)")
        else:
            reasons.append(f"1m not confirming (0)")

    # [OB] Order Block — 2 points
    if direction == "BUY":
        ob_hit = [ob for ob in obs["bullish_obs"] if ob["price_in_ob"]]
        if ob_hit:
            best_ob = sorted(ob_hit,
                             key=lambda x: (x["fresh"], x["strength"]),
                             reverse=True)[0]
            points += 2
            reasons.append(
                f"Bullish OB {best_ob['bottom']:.2f}-{best_ob['top']:.2f} "
                f"str={best_ob['strength']} (+2)")
        else:
            reasons.append("No Bullish OB (0)")
    elif direction == "SELL":
        ob_hit = [ob for ob in obs["bearish_obs"] if ob["price_in_ob"]]
        if ob_hit:
            best_ob = sorted(ob_hit,
                             key=lambda x: (x["fresh"], x["strength"]),
                             reverse=True)[0]
            points += 2
            reasons.append(
                f"Bearish OB {best_ob['bottom']:.2f}-{best_ob['top']:.2f} "
                f"str={best_ob['strength']} (+2)")
        else:
            reasons.append("No Bearish OB (0)")

    # [LIQ] Liquidity Sweep — 2 points
    if direction == "BUY" and liq["sell_swept"]:
        points += 2
        reasons.append("Sell liquidity swept (+2)")
    elif direction == "SELL" and liq["buy_swept"]:
        points += 2
        reasons.append("Buy liquidity swept (+2)")
    else:
        reasons.append("No liquidity sweep (0)")

    # [FVG] Fair Value Gap — 1 point
    if direction == "BUY":
        bull_fvg = [f for f in fvgs if f["type"] == "BULL" and f["retest"]]
        if bull_fvg:
            points += 1
            reasons.append(
                f"Bull FVG {bull_fvg[-1]['bottom']:.2f}-"
                f"{bull_fvg[-1]['top']:.2f} (+1)")
        else:
            reasons.append("No Bull FVG retest (0)")
    elif direction == "SELL":
        bear_fvg = [f for f in fvgs if f["type"] == "BEAR" and f["retest"]]
        if bear_fvg:
            points += 1
            reasons.append(
                f"Bear FVG {bear_fvg[-1]['bottom']:.2f}-"
                f"{bear_fvg[-1]['top']:.2f} (+1)")
        else:
            reasons.append("No Bear FVG retest (0)")

    if direction is None:
        reasons.append("No direction — WAIT")
        return 0, "WAIT", reasons

    reasons.append(f"Total: {points}/10")
    return points, direction, reasons


# ─────────────────────────────────────────────
#  [UPGRADE 4] TP = LIQUIDITY ZONE TARGET
# ─────────────────────────────────────────────
def calc_tp_from_liquidity(side, entry_price, liq, atr):
    """
    TP ko next liquidity zone tak set karo.
    Agar koi zone nahi mila toh ATR-based fallback.
    """
    try:
        if side == "BUY":
            # Buy trade mein TP = nearest buy liquidity level above entry
            levels_above = [lvl for lvl in liq["buy_levels"]
                            if lvl > entry_price * 1.001]
            if levels_above:
                tp = min(levels_above)
                tp_type = f"Liq Zone {tp:.2f}"
                return tp, tp_type

        elif side == "SELL":
            # Sell trade mein TP = nearest sell liquidity level below entry
            levels_below = [lvl for lvl in liq["sell_levels"]
                            if lvl < entry_price * 0.999]
            if levels_below:
                tp = max(levels_below)
                tp_type = f"Liq Zone {tp:.2f}"
                return tp, tp_type

    except Exception as e:
        track_error("calc_tp_from_liquidity", e)

    # Fallback: ATR-based TP
    if side == "BUY":
        tp = entry_price * (1 + (atr * ATR_TP_MULT / entry_price))
    else:
        tp = entry_price * (1 - (atr * ATR_TP_MULT / entry_price))
    return tp, "ATR fallback"


# ─────────────────────────────────────────────
#  PnL CALCULATOR
# ─────────────────────────────────────────────
def calc_pnl(side, entry, exit_price, pos_size):
    if side == "BUY":
        return (exit_price - entry) * pos_size
    else:
        return (entry - exit_price) * pos_size


# ─────────────────────────────────────────────
#  SHARED STATE
# ─────────────────────────────────────────────
trade_state = {
    "position":       None,
    "entry_price":    0.0,
    "entry_time":     None,
    "sl_price":       0.0,
    "tp_price":       0.0,
    "tp_type":        "",
    "pos_size":       0.0,
    "capital_used":   0.0,
    "capital":        CAPITAL,
    "last_signal":    "WAIT",
    "last_conf":      0,
    "last_price":     0.0,
    "last_points":    0,
    "last_tp_zone":   "",
    "last_atr":       0.0,
    "market_ok":      True,
    "market_reason":  "",
}

def update_state(**kwargs):
    with state_lock:
        for key, val in kwargs.items():
            if key in trade_state:
                trade_state[key] = val

def get_state(key):
    with state_lock:
        return trade_state.get(key)


# ─────────────────────────────────────────────
#  PERIODIC UPDATE
# ─────────────────────────────────────────────
def run_periodic_update():
    time.sleep(UPDATE_INTERVAL)
    while True:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with state_lock:
                position     = trade_state["position"]
                price        = trade_state["last_price"]
                capital      = trade_state["capital"]
                points       = trade_state["last_points"]
                entry        = trade_state["entry_price"]
                sl           = trade_state["sl_price"]
                tp           = trade_state["tp_price"]
                tp_type      = trade_state["tp_type"]
                psize        = trade_state["pos_size"]
                etime        = trade_state["entry_time"]
                capital_used = trade_state["capital_used"]
                tp_zone      = trade_state["last_tp_zone"]
                mkt_ok       = trade_state["market_ok"]
                mkt_reason   = trade_state["market_reason"]

            if price == 0:
                time.sleep(UPDATE_INTERVAL)
                continue

            mkt_status = "OK" if mkt_ok else f"SKIP ({mkt_reason})"

            if position is not None and etime is not None:
                pnl      = calc_pnl(position, entry, price, psize)
                dur      = str(datetime.now() - etime).split(".")[0]
                pnl_icon = "+" if pnl >= 0 else ""
                if position == "BUY":
                    tp_dist = ((tp - price) / price) * 100
                    sl_dist = ((price - sl) / price) * 100
                else:
                    tp_dist = ((price - tp) / price) * 100
                    sl_dist = ((sl - price) / price) * 100
                tp_zone_line = f"\nTP Zone : {tp_zone}" if tp_zone else ""
                send_telegram(
                    f"--- SCALP UPDATE ---\n"
                    f"Time    : {now}\n"
                    f"Side    : {position}\n"
                    f"Entry   : {entry:.2f}\n"
                    f"Price   : {price:.2f}\n"
                    f"PnL     : {pnl_icon}{pnl:.4f} USDT\n"
                    f"Capital : {capital:.4f} USDT\n"
                    f"Duration: {dur}\n"
                    f"--------------------\n"
                    f"TP      : {tp:.2f} [{tp_type}] ({tp_dist:.2f}% door)\n"
                    f"SL      : {sl:.2f} ({sl_dist:.2f}% door)\n"
                    f"Score   : {points}/10"
                    f"{tp_zone_line}"
                )
            else:
                send_telegram(
                    f"--- SCALP MARKET ---\n"
                    f"Time    : {now}\n"
                    f"Price   : {price:.2f}\n"
                    f"Score   : {points}/10\n"
                    f"Capital : {capital:.4f} USDT\n"
                    f"Market  : {mkt_status}\n"
                    f"Status  : Next scalp ka wait...\n"
                    f"--------------------"
                )
        except Exception as e:
            track_error("run_periodic_update", e)
        time.sleep(UPDATE_INTERVAL)


# ─────────────────────────────────────────────
#  DAILY REPORT
# ─────────────────────────────────────────────
def run_daily_report():
    last_report_date = None
    while True:
        try:
            ist = timezone(timedelta(hours=5, minutes=30))
            now = datetime.now(ist)
            if now.hour == 23 and now.minute == 59:
                today_date = now.date()
                if last_report_date != today_date:
                    last_report_date = today_date
                    daily   = get_daily_stats()
                    overall = get_overall_stats()
                    if daily and overall:
                        send_telegram(
                            f"--- SCALP DAILY ---\n"
                            f"Date     : {now.strftime('%d/%m/%Y')}\n"
                            f"Trades   : {daily['total']}\n"
                            f"Win      : {daily['wins']}\n"
                            f"Loss     : {daily['losses']}\n"
                            f"Win Rate : {daily['win_rate']}%\n"
                            f"PnL      : {daily['pnl']:+.4f} USDT\n"
                            f"Capital  : {daily['capital']:.4f} USDT\n"
                            f"Best     : +{daily['best']:.4f} USDT\n"
                            f"Worst    : {daily['worst']:.4f} USDT\n"
                            f"--------------------\n"
                            f"OVERALL:\n"
                            f"Trades   : {overall['total']}\n"
                            f"Win Rate : {overall['win_rate']}%\n"
                            f"Total PnL: {overall['pnl']:+.4f} USDT\n"
                            f"Capital  : {overall['capital']:.4f} USDT\n"
                            f"--------------------"
                        )
                    else:
                        send_telegram(
                            f"--- SCALP DAILY ---\n"
                            f"Aaj koi scalp trade nahi hua\n"
                            f"--------------------"
                        )
        except Exception as e:
            track_error("run_daily_report", e)
        time.sleep(30)


# ─────────────────────────────────────────────
#  DECISION ENGINE — 15m + 5m + 1m
# ─────────────────────────────────────────────
def run_decision_engine():
    exchange = get_exchange()
    print("[SCALP DECISION] v4.0 started — 15m+5m+1m | 24/7")

    while True:
        try:
            scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Fetch 3 timeframes — with delay to avoid rate limit
            bars_15m = safe_fetch_ohlcv(exchange, SYMBOL, "15m", 100)
            time.sleep(1.0)
            bars_5m  = safe_fetch_ohlcv(exchange, SYMBOL, "5m", 100)
            time.sleep(1.0)
            bars_1m  = safe_fetch_ohlcv(exchange, SYMBOL, "1m", 100)

            if bars_15m is None or bars_5m is None or bars_1m is None:
                print("[DECISION] Data fetch fail — retry 30s")
                time.sleep(30)
                continue

            def to_df(bars):
                df = pd.DataFrame(
                    bars,
                    columns=["time","open","high","low","close","volume"])
                df["time"] = pd.to_datetime(df["time"], unit="ms")
                return df

            df_15m = to_df(bars_15m)
            df_5m  = to_df(bars_5m)
            df_1m  = to_df(bars_1m)

            if len(df_15m) < 20 or len(df_5m) < 20 or len(df_1m) < 20:
                print("[DECISION] Data insufficient")
                time.sleep(30)
                continue

            current_price = float(df_1m["close"].iloc[-1])
            atr           = calc_atr(df_1m, ATR_PERIOD)

            # [UPGRADE 3] Market condition filter
            mkt_ok, mkt_reason = is_good_market(df_1m, atr)
            update_state(market_ok=mkt_ok, market_reason=mkt_reason)

            if not mkt_ok:
                print(f"[DECISION] Market skip: {mkt_reason}")
                signal_data = {
                    "signal":     "WAIT",
                    "confidence": 0,
                    "score":      0,
                    "atr":        round(atr, 4),
                    "time":       scan_time,
                    "reasons":    [mkt_reason],
                    "liq":        None,
                }
                if not signal_queue.empty():
                    try: signal_queue.get_nowait()
                    except Empty: pass
                signal_queue.put(signal_data)
                update_state(last_signal="WAIT", last_conf=0, last_points=0,
                             last_price=current_price, last_atr=atr)
                time.sleep(DECISION_SCAN)
                continue

            # [UPGRADE 2] 3-timeframe structure
            structure_15m = detect_structure(df_15m, swing_bars=2)
            structure_5m  = detect_structure(df_5m, swing_bars=2)
            structure_1m  = detect_structure(df_1m, swing_bars=2)

            liq  = detect_liquidity(df_1m, lookback=40)
            obs  = detect_order_blocks(df_1m, lookback=40)
            fvgs = detect_fvg(df_1m, lookback=30)

            points, direction, reasons = smart_money_score(
                structure_15m, structure_5m, structure_1m, liq, obs, fvgs
            )

            confidence = int((points / 10) * 100)

            if points >= MIN_SCORE and direction == "BUY":
                signal = "BUY"
            elif points >= MIN_SCORE and direction == "SELL":
                signal = "SELL"
            else:
                signal = "WAIT"

            print(f"[SCALP] {scan_time} | {points}/10 | {signal} | "
                  f"ATR={atr:.2f} | 15m={structure_15m} | "
                  f"5m={structure_5m} | 1m={structure_1m} | "
                  f"Price={current_price:.2f}")

            signal_data = {
                "signal":     signal,
                "confidence": confidence,
                "score":      points,
                "atr":        round(atr, 4),
                "time":       scan_time,
                "reasons":    reasons,
                "liq":        liq,          # [UPGRADE 4] Liquidity zones pass
            }
            if not signal_queue.empty():
                try: signal_queue.get_nowait()
                except Empty: pass
            signal_queue.put(signal_data)

            update_state(
                last_signal=signal,
                last_conf=confidence,
                last_points=points,
                last_price=current_price,
                last_atr=atr,
            )

            try:
                try:
                    with open(LOG_FILE, "r", encoding="utf-8") as f:
                        log = json.load(f)
                except Exception:
                    log = []
                log.append({
                    "time":      scan_time,
                    "signal":    signal,
                    "points":    points,
                    "atr":       round(atr, 4),
                    "price":     current_price,
                    "15m":       structure_15m,
                    "5m":        structure_5m,
                    "1m":        structure_1m,
                    "mkt_ok":    mkt_ok,
                    "reasons":   reasons,
                })
                log = log[-3000:]
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    json.dump(log, f, indent=2)
            except Exception as e:
                track_error("decision_log_write", e)

        except Exception as e:
            track_error("run_decision_engine", e)
            time.sleep(30)

        time.sleep(DECISION_SCAN)


# ─────────────────────────────────────────────
#  EXECUTION ENGINE
# ─────────────────────────────────────────────
def run_execution_engine():
    ex           = get_exchange()
    capital      = load_capital()
    initial_cap  = capital
    position     = None
    entry_price  = 0.0
    entry_time   = None
    pos_size     = 0.0
    sl_price     = 0.0
    tp_price     = 0.0
    tp_type_str  = ""
    capital_used = 0.0
    cooldown_end = None
    last_liq     = None

    print("[SCALP EXECUTE] Waiting for first signal...")
    while signal_queue.empty():
        time.sleep(2)

    print("[SCALP EXECUTE] v4.0 started!")
    send_telegram(
        f"SCALPING BOT v4.0 UPGRADE STARTED\n"
        f"Capital  : {capital:.2f} USDT\n"
        f"Symbol   : {SYMBOL}\n"
        f"Mode     : Paper Trading\n"
        f"Strategy : Smart Money 24/7\n"
        f"Leverage : {LEVERAGE}x\n"
        f"Capital% : {CAPITAL_USE_PCT}%\n"
        f"Min Score: {MIN_SCORE}/10\n"
        f"Max Hold : {MAX_HOLD_SECONDS//60} min\n"
        f"TF       : 15m + 5m + 1m\n"
        f"WS Price : ON (no rate limit)\n"
        f"Mkt Filter: ON\n"
        f"TP Target: Liquidity Zone\n"
        f"Daily SL : {MAX_DAILY_LOSS_PCT}%\n"
        f"TP Zone  : {int(TP_EXIT_MIN_PCT*100)}-{int(TP_EXIT_MAX_PCT*100)}%"
    )

    while True:
        try:
            # Daily loss check
            daily_pnl = get_daily_pnl()
            daily_loss_pct = (abs(daily_pnl) / initial_cap) * 100 \
                             if daily_pnl < 0 else 0.0
            if daily_loss_pct >= MAX_DAILY_LOSS_PCT:
                print(f"[RISK] Daily loss {daily_loss_pct:.2f}% — bot paused 1h")
                send_telegram(
                    f"DAILY LOSS LIMIT HIT\n"
                    f"Loss    : {daily_pnl:.4f} USDT ({daily_loss_pct:.2f}%)\n"
                    f"Bot paused for 1 hour."
                )
                time.sleep(3600)
                initial_cap = capital
                continue

            # Signal from queue
            try:
                data = signal_queue.get_nowait()
                signal     = data.get("signal", "WAIT")
                confidence = data.get("confidence", 0)
                score      = data.get("score", 0.0)
                reason     = " | ".join(data.get("reasons", []))
                atr        = data.get("atr", 0.0)
                last_liq   = data.get("liq", last_liq)
            except Empty:
                signal     = get_state("last_signal") or "WAIT"
                confidence = get_state("last_conf") or 0
                score      = get_state("last_points") or 0
                atr        = get_state("last_atr") or 0.0
                reason     = ""

            # [UPGRADE 1] WebSocket price — fallback to exchange REST
            current_price = get_ws_price()
            if current_price is None:
                print("[WS] Price not available — WebSocket warming up...")
                time.sleep(EXECUTE_SCAN)
                continue

            now = datetime.now().strftime("%H:%M:%S")

            update_state(
                last_price=current_price,
                capital=capital,
                position=position,
                entry_price=entry_price,
                entry_time=entry_time,
                sl_price=sl_price,
                tp_price=tp_price,
                tp_type=tp_type_str,
                pos_size=pos_size,
                capital_used=capital_used,
            )

            # Max Hold check
            if position is not None and entry_time is not None:
                held_secs = (datetime.now() - entry_time).seconds
                if held_secs >= MAX_HOLD_SECONDS:
                    pnl      = calc_pnl(position, entry_price, current_price, pos_size)
                    capital += pnl
                    duration = str(datetime.now() - entry_time).split(".")[0]
                    save_capital(capital)
                    save_trade_history(position, entry_price, current_price,
                                       pnl, capital, duration, "Max Hold")
                    print(f"[MAX HOLD] {MAX_HOLD_SECONDS}s | PnL={pnl:+.4f}")
                    send_telegram(
                        f"SCALP CLOSED — Max Hold\n"
                        f"Side    : {position}\n"
                        f"Entry   : {entry_price:.2f}\n"
                        f"Exit    : {current_price:.2f}\n"
                        f"PnL     : {pnl:+.4f} USDT\n"
                        f"Capital : {capital:.4f} USDT\n"
                        f"Time    : {duration}"
                    )
                    position = None; entry_price = 0.0; entry_time = None
                    pos_size = 0.0; sl_price = 0.0; tp_price = 0.0
                    tp_type_str = ""; capital_used = 0.0
                    cooldown_end = time.time() + COOLDOWN
                    update_state(position=None, capital_used=0.0,
                                 capital=capital, last_tp_zone="", tp_type="")
                    time.sleep(EXECUTE_SCAN)
                    continue

            # TP Zone 70-90% early exit
            if position is not None:
                try:
                    if position == "BUY":
                        tp_range = tp_price - entry_price
                        tp_prog  = (current_price - entry_price) / tp_range \
                                   if tp_range != 0 else 0
                    else:
                        tp_range = entry_price - tp_price
                        tp_prog  = (entry_price - current_price) / tp_range \
                                   if tp_range != 0 else 0

                    if TP_EXIT_MIN_PCT <= tp_prog <= TP_EXIT_MAX_PCT:
                        pts = get_state("last_points")
                        if pts < TP_HOLD_MIN_SCORE:
                            pnl      = calc_pnl(position, entry_price, current_price, pos_size)
                            capital += pnl
                            duration = str(datetime.now() - entry_time).split(".")[0]
                            save_capital(capital)
                            save_trade_history(position, entry_price, current_price,
                                               pnl, capital, duration, "Early Exit")
                            update_state(
                                last_tp_zone=f"TP {tp_prog*100:.0f}% exit | "
                                             f"Score={pts}/10 | PnL={pnl:+.4f}"
                            )
                            print(f"[EARLY EXIT] TP {tp_prog*100:.0f}% | Score={pts}/10 | PnL={pnl:+.4f}")
                            send_telegram(
                                f"SCALP EARLY EXIT\n"
                                f"Side  : {position}\n"
                                f"Entry : {entry_price:.2f}\n"
                                f"Exit  : {current_price:.2f}\n"
                                f"PnL   : {pnl:+.4f} USDT\n"
                                f"Zone  : {tp_prog*100:.0f}%\n"
                                f"Score : {pts}/10 weak"
                            )
                            position = None; entry_price = 0.0; entry_time = None
                            pos_size = 0.0; sl_price = 0.0; tp_price = 0.0
                            tp_type_str = ""; capital_used = 0.0
                            cooldown_end = time.time() + COOLDOWN
                            update_state(position=None, capital_used=0.0,
                                         capital=capital, last_tp_zone="", tp_type="")
                            time.sleep(EXECUTE_SCAN)
                            continue
                        else:
                            update_state(
                                last_tp_zone=f"TP {tp_prog*100:.0f}% zone | "
                                             f"Score={pts}/10 strong — wait"
                            )
                    else:
                        update_state(last_tp_zone="")
                except Exception as e:
                    track_error("tp_zone_check", e)

            # ATR-based Trailing SL
            if position is not None:
                try:
                    current_atr = get_state("last_atr") or atr
                    if current_atr > 0 and current_price > 0:
                        trail_dist_pct = (current_atr * 0.5 / current_price) * 100
                    else:
                        trail_dist_pct = 0.2

                    if position == "BUY":
                        p_pct = ((current_price - entry_price) / entry_price) * 100
                        if p_pct >= 0.3:
                            new_sl = current_price * (1 - trail_dist_pct / 100)
                            if new_sl > sl_price:
                                old_sl = sl_price
                                sl_price = new_sl
                                update_state(sl_price=sl_price)
                                print(f"[TRAIL] BUY SL {old_sl:.2f} → {sl_price:.2f}")
                    elif position == "SELL":
                        p_pct = ((entry_price - current_price) / entry_price) * 100
                        if p_pct >= 0.3:
                            new_sl = current_price * (1 + trail_dist_pct / 100)
                            if new_sl < sl_price:
                                old_sl = sl_price
                                sl_price = new_sl
                                update_state(sl_price=sl_price)
                                print(f"[TRAIL] SELL SL {old_sl:.2f} → {sl_price:.2f}")
                except Exception as e:
                    track_error("trailing_sl", e)

            # SL / TP Check
            if position is not None:
                hit_sl = (position == "BUY"  and current_price <= sl_price) or \
                         (position == "SELL" and current_price >= sl_price)
                hit_tp = (position == "BUY"  and current_price >= tp_price) or \
                         (position == "SELL" and current_price <= tp_price)

                if hit_sl or hit_tp:
                    label    = "STOP LOSS" if hit_sl else "TAKE PROFIT"
                    pnl      = calc_pnl(position, entry_price, current_price, pos_size)
                    capital += pnl
                    duration = str(datetime.now() - entry_time).split(".")[0]
                    save_capital(capital)
                    save_trade_history(position, entry_price, current_price,
                                       pnl, capital, duration, label)
                    print(f"[SCALP] {label} | {position} | "
                          f"PnL={pnl:+.4f} | Capital={capital:.4f}")
                    send_telegram(
                        f"SCALP CLOSED — {label}\n"
                        f"Side    : {position}\n"
                        f"Entry   : {entry_price:.2f}\n"
                        f"Exit    : {current_price:.2f}\n"
                        f"TP Type : {tp_type_str}\n"
                        f"PnL     : {pnl:+.4f} USDT\n"
                        f"Capital : {capital:.4f} USDT\n"
                        f"Time    : {duration}"
                    )
                    position = None; entry_price = 0.0; entry_time = None
                    pos_size = 0.0; sl_price = 0.0; tp_price = 0.0
                    tp_type_str = ""; capital_used = 0.0
                    cooldown_end = time.time() + COOLDOWN
                    update_state(position=None, capital_used=0.0,
                                 capital=capital, last_tp_zone="", tp_type="")
                    time.sleep(EXECUTE_SCAN)
                    continue

            # Cooldown
            if cooldown_end is not None and time.time() < cooldown_end:
                remaining = int(cooldown_end - time.time())
                print(f"[{now}] Cooldown {remaining}s | Price={current_price:.2f}")
                time.sleep(EXECUTE_SCAN)
                continue

            # Entry
            if position is None:
                if signal in ["BUY", "SELL"] and int(score) >= MIN_SCORE:
                    if atr > 0:
                        # [UPGRADE 4] Tight SL (ATR × 1.0)
                        sl_pct = (atr * ATR_SL_MULT / current_price) * 100
                    else:
                        sl_pct = 0.3

                    capital_used = capital * (CAPITAL_USE_PCT / 100)
                    pos_size     = (capital_used * LEVERAGE) / current_price
                    entry_price  = current_price
                    entry_time   = datetime.now()
                    position     = signal
                    cooldown_end = None

                    if signal == "BUY":
                        sl_price = entry_price * (1 - sl_pct / 100)
                    else:
                        sl_price = entry_price * (1 + sl_pct / 100)

                    # [UPGRADE 4] TP = Liquidity Zone
                    if last_liq is not None:
                        tp_price, tp_type_str = calc_tp_from_liquidity(
                            signal, entry_price, last_liq, atr
                        )
                    else:
                        tp_pct = (atr * ATR_TP_MULT / current_price) * 100
                        if signal == "BUY":
                            tp_price = entry_price * (1 + tp_pct / 100)
                        else:
                            tp_price = entry_price * (1 - tp_pct / 100)
                        tp_type_str = "ATR fallback"

                    update_state(tp_type=tp_type_str)

                    print(f"[SCALP] OPENED | {position} | "
                          f"Entry={entry_price:.2f} | "
                          f"SL={sl_price:.2f} | "
                          f"TP={tp_price:.2f} [{tp_type_str}] | "
                          f"Score={int(score)}/10")
                    send_telegram(
                        f"SCALP OPENED\n"
                        f"Side    : {position}\n"
                        f"Entry   : {entry_price:.2f}\n"
                        f"SL      : {sl_price:.2f}\n"
                        f"TP      : {tp_price:.2f} [{tp_type_str}]\n"
                        f"ATR     : {atr:.2f}\n"
                        f"Capital : {capital_used:.2f} USDT\n"
                        f"Score   : {int(score)}/10\n"
                        f"Reason  : {reason[:250]}"
                    )
                else:
                    mkt_status = "" if get_state("market_ok") else \
                                 f" | Mkt={get_state('market_reason')[:20]}"
                    print(f"[{now}] WAIT | Score={int(score)}/10 | "
                          f"Price={current_price:.2f}{mkt_status}")
            else:
                pnl_now = calc_pnl(position, entry_price, current_price, pos_size)
                print(f"[{now}] Holding {position} | "
                      f"PnL={pnl_now:+.4f} | Price={current_price:.2f}")

        except Exception as e:
            err_msg = str(e)
            track_error("run_execution_engine", e)
            if "429" in err_msg or "Too Many" in err_msg:
                print("[RATE LIMIT] 60s wait...")
                time.sleep(60)
            elif "connection" in err_msg.lower():
                print("[CONNECTION] 30s wait...")
                time.sleep(30)
            else:
                time.sleep(10)

        time.sleep(EXECUTE_SCAN)


# ─────────────────────────────────────────────
#  START
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  SCALPING BOT v4.0 — Full Upgrade Edition")
    print("  Strategy  : Smart Money 24/7")
    print("  Timeframes: 15m + 5m + 1m")
    print("  Min Score : 7/10")
    print("  WebSocket : ON  (no rate limit)")
    print("  Mkt Filter: ON")
    print("  TP Target : Liquidity Zone")
    print("  Tight SL  : ATR x 1.0")
    print("=" * 60)

    t1 = threading.Thread(target=run_server,           daemon=True)
    t2 = threading.Thread(target=run_websocket,        daemon=True)  # [UPGRADE 1]
    t3 = threading.Thread(target=run_decision_engine,  daemon=True)
    t4 = threading.Thread(target=run_execution_engine, daemon=True)
    t5 = threading.Thread(target=run_periodic_update,  daemon=True)
    t6 = threading.Thread(target=run_daily_report,     daemon=True)

    for t in [t1, t2, t3, t4, t5, t6]:
        t.start()

    print("[INFO] All engines started!")
    print("[INFO] Flask      : port 8081")
    print("[INFO] WebSocket  : Binance Futures live feed")
    print("[INFO] Decision   : har 90s (15m+5m+1m)")
    print("[INFO] Execute    : har 15s (WS price)")
    print("[INFO] Max Hold   : 3 min")
    print("[INFO] Daily SL   : 5%")
    print("[INFO] 24/7       : ON")

    while True:
        time.sleep(60)
