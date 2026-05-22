"""
SCALPING BOT v3.0 — Fixed Edition
Strategy  : Smart Money (OB + Liquidity + FVG)
Sessions  : 24/7
Min Score : 6/8
Capital   : 90% per trade  [intentionally kept as-is]
TP Zone   : 70-90% early exit
Max Hold  : 3 min
"""

import threading
import time
import os
from queue import Queue, Empty
from flask import Flask
from dotenv import load_dotenv

# ─────────────────────────────────────────────
#  FIX #1 — .env se API keys load karo
# ─────────────────────────────────────────────
load_dotenv()

API_KEY    = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_SECRET", "")
BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "8161773850:AAFcWw3UnlSe2TrMooB2uvgZQZUqIW0zW2w")
CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "7102976298")

if not all([API_KEY, API_SECRET, BOT_TOKEN, CHAT_ID]):
    print("[WARN] .env mein kuch keys missing hain — check karo!")

app = Flask(__name__)

@app.route('/')
def home():
    return "Scalping Bot v3.0 Fixed Running!"

def run_server():
    app.run(host='0.0.0.0', port=8081)


import ccxt
import pandas as pd
import numpy as np
import json
import requests
import shutil
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
SYMBOL           = "ETH/USDT:USDT"

CAPITAL          = 100.0
CAPITAL_USE_PCT  = 90
LEVERAGE         = 10
MIN_SCORE        = 6
MIN_CONFIDENCE   = int((MIN_SCORE / 8) * 100)

MAX_DAILY_LOSS_PCT = 5.0

EXECUTE_SCAN     = 8
DECISION_SCAN    = 60
COOLDOWN         = 60
MAX_HOLD_SECONDS = 180

ATR_PERIOD       = 7
ATR_SL_MULT      = 1.5
ATR_TP_MULT      = 1.5

TP_EXIT_MIN_PCT   = 0.70
TP_EXIT_MAX_PCT   = 0.90
TP_HOLD_MIN_SCORE = 7

UPDATE_INTERVAL  = 1800

signal_queue = Queue(maxsize=1)

LOG_FILE       = "scalping_log.json"
CAPITAL_FILE   = "scalping_capital.txt"
TRADE_HISTORY  = "scalping_history.json"

state_lock   = threading.Lock()
error_counts = {}


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
        "rateLimit":       100,
    })
    ex.load_markets()
    print("[INFO] Binance USDT-M Futures connected")
    return ex


def safe_fetch_ticker(ex, symbol, retries=3):
    for i in range(retries):
        try:
            ticker = ex.fetch_ticker(symbol)
            return float(ticker["last"])
        except Exception as e:
            if "429" in str(e) or "Too Many" in str(e):
                wait = (i + 1) * 30
                print(f"[RATE LIMIT] Wait {wait}s...")
                time.sleep(wait)
            else:
                print(f"[TICKER ERROR] {e}")
                time.sleep(5)
    return None


def safe_fetch_ohlcv(ex, symbol, tf, limit, retries=3):
    for i in range(retries):
        try:
            bars = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
            return bars
        except Exception as e:
            if "429" in str(e) or "Too Many" in str(e):
                wait = (i + 1) * 30
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
#  ORDER BLOCKS — FIX #4
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
#  LIQUIDITY — FIX #5
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
#  SMART MONEY SCORE — FIX #10
# ─────────────────────────────────────────────
def smart_money_score(structure_5m, structure_1m, liq, obs, fvgs):
    points    = 0
    direction = None
    reasons   = []

    if structure_5m == "BULL":
        points += 2
        direction = "BUY"
        reasons.append("5m BULL (+2)")
    elif structure_5m == "BEAR":
        points += 2
        direction = "SELL"
        reasons.append("5m BEAR (+2)")
    else:
        reasons.append("5m RANGE (0) — waiting for breakout confirmation")
        if structure_1m == "BULL":
            direction = "BUY"
        elif structure_1m == "BEAR":
            direction = "SELL"

    if direction is not None:
        if (direction == "BUY"  and structure_1m == "BULL") or \
           (direction == "SELL" and structure_1m == "BEAR"):
            points += 1
            reasons.append(f"1m confirms {direction} (+1)")
        else:
            reasons.append(f"1m not confirming (0)")

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

    if direction == "BUY" and liq["sell_swept"]:
        points += 2
        reasons.append("Sell liquidity swept (+2)")
    elif direction == "SELL" and liq["buy_swept"]:
        points += 2
        reasons.append("Buy liquidity swept (+2)")
    else:
        reasons.append("No liquidity sweep (0)")

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

    reasons.append(f"Total: {points}/8")
    return points, direction, reasons


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
    "position":     None,
    "entry_price":  0.0,
    "entry_time":   None,
    "sl_price":     0.0,
    "tp_price":     0.0,
    "pos_size":     0.0,
    "capital_used": 0.0,
    "capital":      CAPITAL,
    "last_signal":  "WAIT",
    "last_conf":    0,
    "last_price":   0.0,
    "last_points":  0,
    "last_tp_zone": "",
    "last_atr":     0.0,
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
                psize        = trade_state["pos_size"]
                etime        = trade_state["entry_time"]
                capital_used = trade_state["capital_used"]
                tp_zone      = trade_state["last_tp_zone"]

            if price == 0:
                time.sleep(UPDATE_INTERVAL)
                continue

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
                    f"TP      : {tp:.2f} ({tp_dist:.2f}% door)\n"
                    f"SL      : {sl:.2f} ({sl_dist:.2f}% door)\n"
                    f"Score   : {points}/8"
                    f"{tp_zone_line}"
                )
            else:
                send_telegram(
                    f"--- SCALP MARKET ---\n"
                    f"Time    : {now}\n"
                    f"Price   : {price:.2f}\n"
                    f"Score   : {points}/8\n"
                    f"Capital : {capital:.4f} USDT\n"
                    f"Status  : Next scalp ka wait...\n"
                    f"--------------------"
                )
        except Exception as e:
            track_error("run_periodic_update", e)
        time.sleep(UPDATE_INTERVAL)


# ─────────────────────────────────────────────
#  DAILY REPORT — FIX #9
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
#  DECISION ENGINE — FIX #3
# ─────────────────────────────────────────────
def run_decision_engine():
    exchange = get_exchange()
    print("[SCALP DECISION] v3.0 Fixed started — 24/7")

    while True:
        try:
            scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            bars_5m = safe_fetch_ohlcv(exchange, SYMBOL, "5m", 100)
            time.sleep(0.5)
            bars_1m = safe_fetch_ohlcv(exchange, SYMBOL, "1m", 100)

            if bars_5m is None or bars_1m is None:
                print("[DECISION] Data fetch fail — retry 30s")
                time.sleep(30)
                continue

            df_5m = pd.DataFrame(
                bars_5m, columns=["time","open","high","low","close","volume"])
            df_1m = pd.DataFrame(
                bars_1m, columns=["time","open","high","low","close","volume"])

            if len(df_5m) < 20 or len(df_1m) < 20:
                print("[DECISION] Data insufficient")
                time.sleep(30)
                continue

            df_5m["time"] = pd.to_datetime(df_5m["time"], unit="ms")
            df_1m["time"] = pd.to_datetime(df_1m["time"], unit="ms")

            current_price = float(df_1m["close"].iloc[-1])
            atr           = calc_atr(df_1m, ATR_PERIOD)

            structure_5m = detect_structure(df_5m, swing_bars=2)
            structure_1m = detect_structure(df_1m, swing_bars=2)
            liq          = detect_liquidity(df_1m, lookback=40)
            obs          = detect_order_blocks(df_1m, lookback=40)
            fvgs         = detect_fvg(df_1m, lookback=30)

            points, direction, reasons = smart_money_score(
                structure_5m, structure_1m, liq, obs, fvgs
            )

            confidence = int((points / 8) * 100)

            if points >= MIN_SCORE and direction == "BUY":
                signal = "BUY"
            elif points >= MIN_SCORE and direction == "SELL":
                signal = "SELL"
            else:
                signal = "WAIT"

            print(f"[SCALP] {scan_time} | {points}/8 | {signal} | "
                  f"ATR={atr:.2f} | Price={current_price:.2f}")

            signal_data = {
                "signal":     signal,
                "confidence": confidence,
                "score":      points,
                "atr":        round(atr, 4),
                "time":       scan_time,
                "reasons":    reasons,
            }
            if not signal_queue.empty():
                try:
                    signal_queue.get_nowait()
                except Empty:
                    pass
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
                    "time":    scan_time,
                    "signal":  signal,
                    "points":  points,
                    "atr":     round(atr, 4),
                    "price":   current_price,
                    "reasons": reasons,
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
    capital_used = 0.0
    cooldown_end = None

    print("[SCALP EXECUTE] Waiting for first signal...")
    while signal_queue.empty():
        time.sleep(2)

    print("[SCALP EXECUTE] v3.0 Fixed started!")
    send_telegram(
        f"SCALPING BOT v3.0 FIXED STARTED\n"
        f"Capital  : {capital:.2f} USDT\n"
        f"Symbol   : {SYMBOL}\n"
        f"Mode     : Paper Trading\n"
        f"Strategy : Smart Money 24/7\n"
        f"Leverage : {LEVERAGE}x\n"
        f"Capital% : {CAPITAL_USE_PCT}%\n"
        f"Min Score: {MIN_SCORE}/8\n"
        f"Max Hold : {MAX_HOLD_SECONDS//60} min\n"
        f"Daily Loss Limit: {MAX_DAILY_LOSS_PCT}%\n"
        f"TP Zone  : {int(TP_EXIT_MIN_PCT*100)}-{int(TP_EXIT_MAX_PCT*100)}%"
    )

    while True:
        try:
            # FIX #2 — Daily loss check
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

            # FIX #3 — Queue se signal
            try:
                data = signal_queue.get_nowait()
                signal     = data.get("signal", "WAIT")
                confidence = data.get("confidence", 0)
                score      = data.get("score", 0.0)
                reason     = " | ".join(data.get("reasons", []))
                atr        = data.get("atr", 0.0)
            except Empty:
                signal     = get_state("last_signal") or "WAIT"
                confidence = get_state("last_conf") or 0
                score      = get_state("last_points") or 0
                atr        = get_state("last_atr") or 0.0
                reason     = ""

            current_price = safe_fetch_ticker(ex, SYMBOL)
            if current_price is None:
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
                pos_size=pos_size,
                capital_used=capital_used,
            )

            # Max Hold
            if position is not None and entry_time is not None:
                held_secs = (datetime.now() - entry_time).seconds
                if held_secs >= MAX_HOLD_SECONDS:
                    pnl      = calc_pnl(position, entry_price,
                                        current_price, pos_size)
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
                    position     = None; entry_price = 0.0; entry_time  = None
                    pos_size     = 0.0;  sl_price    = 0.0;  tp_price   = 0.0
                    capital_used = 0.0
                    cooldown_end = time.time() + COOLDOWN
                    update_state(position=None, capital_used=0.0,
                                 capital=capital, last_tp_zone="")
                    time.sleep(EXECUTE_SCAN)
                    continue

            # TP Zone 70-90%
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
                            pnl      = calc_pnl(position, entry_price,
                                                current_price, pos_size)
                            capital += pnl
                            duration = str(datetime.now() - entry_time).split(".")[0]
                            save_capital(capital)
                            save_trade_history(position, entry_price, current_price,
                                               pnl, capital, duration, "Early Exit")
                            update_state(
                                last_tp_zone=f"TP {tp_prog*100:.0f}% exit | "
                                             f"Score={pts}/8 | PnL={pnl:+.4f}"
                            )
                            print(f"[EARLY EXIT] TP {tp_prog*100:.0f}% | "
                                  f"Score={pts}/8 | PnL={pnl:+.4f}")
                            send_telegram(
                                f"SCALP EARLY EXIT\n"
                                f"Side  : {position}\n"
                                f"Entry : {entry_price:.2f}\n"
                                f"Exit  : {current_price:.2f}\n"
                                f"PnL   : {pnl:+.4f} USDT\n"
                                f"Zone  : {tp_prog*100:.0f}%\n"
                                f"Score : {pts}/8 weak"
                            )
                            position     = None; entry_price = 0.0; entry_time  = None
                            pos_size     = 0.0;  sl_price    = 0.0;  tp_price   = 0.0
                            capital_used = 0.0
                            cooldown_end = time.time() + COOLDOWN
                            update_state(position=None, capital_used=0.0,
                                         capital=capital, last_tp_zone="")
                            time.sleep(EXECUTE_SCAN)
                            continue
                        else:
                            update_state(
                                last_tp_zone=f"TP {tp_prog*100:.0f}% zone | "
                                             f"Score={pts}/8 strong — wait"
                            )
                    else:
                        update_state(last_tp_zone="")
                except Exception as e:
                    track_error("tp_zone_check", e)

            # FIX #6 — ATR-based Trailing SL
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
                                old_sl   = sl_price
                                sl_price = new_sl
                                update_state(sl_price=sl_price)
                                print(f"[TRAIL] BUY SL {old_sl:.2f} → {sl_price:.2f}")
                    elif position == "SELL":
                        p_pct = ((entry_price - current_price) / entry_price) * 100
                        if p_pct >= 0.3:
                            new_sl = current_price * (1 + trail_dist_pct / 100)
                            if new_sl < sl_price:
                                old_sl   = sl_price
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
                    pnl      = calc_pnl(position, entry_price,
                                        current_price, pos_size)
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
                        f"PnL     : {pnl:+.4f} USDT\n"
                        f"Capital : {capital:.4f} USDT\n"
                        f"Time    : {duration}"
                    )
                    position     = None; entry_price = 0.0; entry_time  = None
                    pos_size     = 0.0;  sl_price    = 0.0;  tp_price   = 0.0
                    capital_used = 0.0
                    cooldown_end = time.time() + COOLDOWN
                    update_state(position=None, capital_used=0.0,
                                 capital=capital, last_tp_zone="")
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
                        sl_pct = (atr * ATR_SL_MULT / current_price) * 100
                        tp_pct = (atr * ATR_TP_MULT / current_price) * 100
                    else:
                        sl_pct = 0.3
                        tp_pct = 0.3

                    capital_used = capital * (CAPITAL_USE_PCT / 100)
                    pos_size     = (capital_used * LEVERAGE) / current_price
                    entry_price  = current_price
                    entry_time   = datetime.now()
                    position     = signal
                    cooldown_end = None

                    if signal == "BUY":
                        sl_price = entry_price * (1 - sl_pct / 100)
                        tp_price = entry_price * (1 + tp_pct / 100)
                    else:
                        sl_price = entry_price * (1 + sl_pct / 100)
                        tp_price = entry_price * (1 - tp_pct / 100)

                    print(f"[SCALP] OPENED | {position} | "
                          f"Entry={entry_price:.2f} | "
                          f"SL={sl_price:.2f} | "
                          f"TP={tp_price:.2f} | "
                          f"Score={int(score)}/8")
                    send_telegram(
                        f"SCALP OPENED\n"
                        f"Side    : {position}\n"
                        f"Entry   : {entry_price:.2f}\n"
                        f"SL      : {sl_price:.2f}\n"
                        f"TP      : {tp_price:.2f}\n"
                        f"ATR     : {atr:.2f}\n"
                        f"Capital : {capital_used:.2f} USDT\n"
                        f"Score   : {int(score)}/8\n"
                        f"Reason  : {reason[:250]}"
                    )
                else:
                    print(f"[{now}] WAIT | Score={int(score)}/8 | "
                          f"Price={current_price:.2f}")
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
    print("=" * 55)
    print("  SCALPING BOT v3.0 — Fixed Edition")
    print("  Strategy : Smart Money 24/7")
    print("  Min Score: 6/8")
    print("  Capital  : 90%")
    print("  TP Zone  : 70-90%")
    print("  Fixes    : #1 #2 #3 #4 #5 #6 #7 #8 #9 #10")
    print("=" * 55)

    t1 = threading.Thread(target=run_server,           daemon=True)
    t2 = threading.Thread(target=run_decision_engine,  daemon=True)
    t3 = threading.Thread(target=run_execution_engine, daemon=True)
    t4 = threading.Thread(target=run_periodic_update,  daemon=True)
    t5 = threading.Thread(target=run_daily_report,     daemon=True)

    for t in [t1, t2, t3, t4, t5]:
        t.start()

    print("[INFO] All engines started!")
    print("[INFO] Flask    : port 8081")
    print("[INFO] Decision : har 60s  (Queue-based)")
    print("[INFO] Execute  : har 8s")
    print("[INFO] Max Hold : 3 min")
    print("[INFO] Daily SL : 5%")
    print("[INFO] 24/7     : ON")

    while True:
        time.sleep(60)
