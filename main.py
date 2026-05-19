"""
TRADE BOT v6.0 — Main Entry Point
New Features:
1. Win Rate Tracker — daily report raat 11:59 PM
2. Market Hours Filter — sirf London/NY session
3. Dynamic SL/TP — ATR based
"""

import threading
import time
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Trade Bot Running! v6.0"

def run_server():
    app.run(host='0.0.0.0', port=8080)


import ccxt
import pandas as pd
import numpy as np
import json
import requests
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
SYMBOL           = "ETH/USDT:USDT"
API_KEY          = ""
API_SECRET       = ""

TIMEFRAMES = {
    "1w":  0.30,
    "1d":  0.25,
    "4h":  0.25,
    "1h":  0.20,
}

DECISION_SCAN    = 300
OUTPUT_FILE      = "decision_output.txt"
LOG_FILE         = "decision_log.json"
CAPITAL_FILE     = "capital.txt"
TRADE_HISTORY    = "trade_history.json"
BUY_THRESHOLD    =  0.25
SELL_THRESHOLD   = -0.25
FVG_LOOKBACK     = 50
MIN_FVG_SIZE     = 0.1

BOT_TOKEN        = "8161773850:AAFcWw3UnlSe2TrMooB2uvgZQZUqIW0zW2w"
CHAT_ID          = "7102976298"
CAPITAL          = 10000
RISK_PERCENT     = 5
LEVERAGE         = 5
MIN_CONFIDENCE   = 50
EXECUTE_SCAN     = 10

TRAILING_STOP    = True
TRAIL_TRIGGER    = 0.4
TRAIL_OFFSET     = 0.3

UPDATE_INTERVAL  = 1800
COOLDOWN         = 900
MIN_SCORE_POINTS = 6

# ATR Settings
ATR_PERIOD       = 14
ATR_SL_MULT      = 1.5    # SL = ATR * 1.5
ATR_TP_MULT      = 1.5    # TP = ATR * 1.5 (1:1 RR)

# Market Hours (IST = UTC+5:30)
TRADING_SESSIONS = [
    (12, 30, 20, 30),   # London: 12:30 PM - 8:30 PM IST
    (18, 30,  0, 30),   # New York: 6:30 PM - 12:30 AM IST
]

# Thread safety
state_lock = threading.Lock()


# ─────────────────────────────────────────────
#  MARKET HOURS CHECK
# ─────────────────────────────────────────────
def is_trading_hours():
    """
    Sirf London aur NY session mein trade karo.
    IST mein:
      London  : 12:30 PM — 8:30 PM
      New York: 6:30 PM  — 12:30 AM
    """
    ist   = timezone(timedelta(hours=5, minutes=30))
    now   = datetime.now(ist)
    h, m  = now.hour, now.minute
    mins  = h * 60 + m

    # London session: 12:30 - 20:30
    london_start = 12 * 60 + 30
    london_end   = 20 * 60 + 30

    # NY session: 18:30 - 24:30 (midnight cross)
    ny_start = 18 * 60 + 30
    ny_end   = 24 * 60 + 30

    in_london = london_start <= mins <= london_end
    in_ny     = mins >= ny_start or mins <= 30  # midnight cross

    return in_london or in_ny

def next_session_time():
    """Agla session kab shuru hoga"""
    ist  = timezone(timedelta(hours=5, minutes=30))
    now  = datetime.now(ist)
    h, m = now.hour, now.minute
    mins = h * 60 + m

    if mins < 12 * 60 + 30:
        return "12:30 PM IST (London)"
    elif mins < 18 * 60 + 30:
        return "06:30 PM IST (New York)"
    else:
        return "12:30 PM IST kal (London)"


# ─────────────────────────────────────────────
#  ATR CALCULATION
# ─────────────────────────────────────────────
def calc_atr(df, period=14):
    """
    ATR = Average True Range
    Market kitna volatile hai yeh batata hai
    """
    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low  - close.shift(1)).abs()

    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    return atr.iloc[-1]


# ─────────────────────────────────────────────
#  CAPITAL SAVE / LOAD
# ─────────────────────────────────────────────
def load_capital():
    try:
        with open(CAPITAL_FILE, "r") as f:
            cap = float(f.read().strip())
            print(f"[CAPITAL] Loaded: {cap} USDT")
            return cap
    except:
        print(f"[CAPITAL] Default {CAPITAL} USDT")
        return CAPITAL

def save_capital(capital):
    try:
        with open(CAPITAL_FILE, "w") as f:
            f.write(str(round(capital, 4)))
    except Exception as e:
        print(f"[CAPITAL SAVE ERROR] {e}")


# ─────────────────────────────────────────────
#  TRADE HISTORY — WIN RATE TRACKER
# ─────────────────────────────────────────────
def save_trade_history(side, entry, exit_price, pnl, capital, duration, label):
    """Har trade ko history mein save karta hai"""
    try:
        try:
            with open(TRADE_HISTORY, "r", encoding="utf-8") as f:
                history = json.load(f)
        except:
            history = []

        trade = {
            "date":     datetime.now().strftime("%d/%m/%Y"),
            "time":     datetime.now().strftime("%H:%M:%S"),
            "side":     side,
            "entry":    round(entry, 2),
            "exit":     round(exit_price, 2),
            "pnl":      round(pnl, 2),
            "capital":  round(capital, 2),
            "duration": duration,
            "result":   "WIN" if pnl > 0 else "LOSS",
            "label":    label,
        }
        history.append(trade)

        with open(TRADE_HISTORY, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)

    except Exception as e:
        print(f"[HISTORY ERROR] {e}")


def get_daily_stats():
    """Aaj ke trades ki stats nikalta hai"""
    try:
        with open(TRADE_HISTORY, "r", encoding="utf-8") as f:
            history = json.load(f)
    except:
        return None

    today  = datetime.now().strftime("%d/%m/%Y")
    trades = [t for t in history if t["date"] == today]

    if not trades:
        return None

    total   = len(trades)
    wins    = len([t for t in trades if t["result"] == "WIN"])
    losses  = total - wins
    win_rate = round((wins / total) * 100, 1) if total > 0 else 0
    daily_pnl = round(sum(t["pnl"] for t in trades), 2)
    best      = round(max(t["pnl"] for t in trades), 2)
    worst     = round(min(t["pnl"] for t in trades), 2)
    capital   = trades[-1]["capital"]

    return {
        "total":    total,
        "wins":     wins,
        "losses":   losses,
        "win_rate": win_rate,
        "pnl":      daily_pnl,
        "best":     best,
        "worst":    worst,
        "capital":  capital,
    }


def get_overall_stats():
    """Sabhi trades ki overall stats"""
    try:
        with open(TRADE_HISTORY, "r", encoding="utf-8") as f:
            history = json.load(f)
    except:
        return None

    if not history:
        return None

    total    = len(history)
    wins     = len([t for t in history if t["result"] == "WIN"])
    losses   = total - wins
    win_rate = round((wins / total) * 100, 1) if total > 0 else 0
    total_pnl = round(sum(t["pnl"] for t in history), 2)
    best      = round(max(t["pnl"] for t in history), 2)
    worst     = round(min(t["pnl"] for t in history), 2)
    capital   = history[-1]["capital"]

    return {
        "total":    total,
        "wins":     wins,
        "losses":   losses,
        "win_rate": win_rate,
        "pnl":      total_pnl,
        "best":     best,
        "worst":    worst,
        "capital":  capital,
    }


# ─────────────────────────────────────────────
#  EXCHANGE
# ─────────────────────────────────────────────
def get_exchange():
    ex = ccxt.binanceusdm({
        "apiKey":          API_KEY,
        "secret":          API_SECRET,
        "enableRateLimit": True,
    })
    ex.load_markets()
    print("[INFO] Binance USDT-M Futures connected")
    return ex


# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for attempt in range(3):
        try:
            r = requests.post(url, data={"chat_id": CHAT_ID, "text": message}, timeout=15)
            if r.status_code == 200:
                return
        except Exception as e:
            print(f"[WARN] Telegram attempt {attempt+1}/3: {e}")
            time.sleep(3)


# ─────────────────────────────────────────────
#  MARKET STRUCTURE
# ─────────────────────────────────────────────
def detect_structure(df, swing_bars=5):
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


# ─────────────────────────────────────────────
#  KEY LEVELS
# ─────────────────────────────────────────────
def detect_key_levels(df, current_price, lookback=100):
    levels     = []
    recent     = df.tail(lookback)
    highs      = recent["high"].values
    lows       = recent["low"].values
    n          = len(highs)
    swing_bars = 5
    for i in range(swing_bars, n - swing_bars):
        if highs[i] == max(highs[i - swing_bars: i + swing_bars + 1]):
            levels.append(highs[i])
        if lows[i] == min(lows[i - swing_bars: i + swing_bars + 1]):
            levels.append(lows[i])
    base = round(current_price / 100) * 100
    for r in range(-3, 4):
        levels.append(base + r * 100)
    near_level = False
    for level in levels:
        dist_pct = abs(current_price - level) / current_price * 100
        if dist_pct <= 0.3:
            near_level = True
            break
    return near_level, levels


# ─────────────────────────────────────────────
#  FVG DETECTION
# ─────────────────────────────────────────────
def detect_fvg(df, lookback=50, min_gap_pct=0.1):
    fvgs          = []
    recent        = df.tail(lookback).reset_index(drop=True)
    n             = len(recent)
    current_price = recent["close"].iloc[-1]
    for i in range(2, n):
        c1 = recent.iloc[i - 2]
        c3 = recent.iloc[i]
        if c1["high"] < c3["low"]:
            gap_bottom = c1["high"]
            gap_top    = c3["low"]
            gap_size   = ((gap_top - gap_bottom) / gap_bottom) * 100
            if gap_size >= min_gap_pct:
                fvgs.append({
                    "type":   "BULL",
                    "top":    round(gap_top, 4),
                    "bottom": round(gap_bottom, 4),
                    "mid":    round((gap_top + gap_bottom) / 2, 4),
                    "size":   round(gap_size, 3),
                    "fresh":  (i >= n - 10),
                    "retest": (current_price >= gap_bottom * 0.998 and
                               current_price <= gap_top * 1.002),
                    "filled": (current_price <= gap_top and
                               current_price >= gap_bottom),
                })
        elif c1["low"] > c3["high"]:
            gap_top    = c1["low"]
            gap_bottom = c3["high"]
            gap_size   = ((gap_top - gap_bottom) / gap_bottom) * 100
            if gap_size >= min_gap_pct:
                fvgs.append({
                    "type":   "BEAR",
                    "top":    round(gap_top, 4),
                    "bottom": round(gap_bottom, 4),
                    "mid":    round((gap_top + gap_bottom) / 2, 4),
                    "size":   round(gap_size, 3),
                    "fresh":  (i >= n - 10),
                    "retest": (current_price >= gap_bottom * 0.998 and
                               current_price <= gap_top * 1.002),
                    "filled": (current_price >= gap_bottom and
                               current_price <= gap_top),
                })
    return fvgs


# ─────────────────────────────────────────────
#  FVG SIGNAL
# ─────────────────────────────────────────────
def fvg_signal(fvgs, structure, current_price):
    if not fvgs:
        return 0.0, "No FVG found"
    bull_fvgs = [f for f in fvgs if f["type"] == "BULL"]
    bear_fvgs = [f for f in fvgs if f["type"] == "BEAR"]
    score     = 0.0
    reasons   = []
    if structure == "BULL" and bull_fvgs:
        fresh = [f for f in bull_fvgs if f["fresh"]]
        if fresh:
            score += 0.8
            reasons.append(f"Fresh Bullish FVG {fresh[-1]['bottom']:.2f}-{fresh[-1]['top']:.2f}")
        retest = [f for f in bull_fvgs if f["retest"] and not f["fresh"]]
        if retest:
            score += 0.6
            reasons.append(f"Bullish FVG retest {retest[-1]['bottom']:.2f}-{retest[-1]['top']:.2f}")
        unfilled = [f for f in bull_fvgs if not f["filled"] and current_price > f["top"]]
        if unfilled:
            score += 0.2
            reasons.append(f"Unfilled Bull FVG support {unfilled[-1]['bottom']:.2f}-{unfilled[-1]['top']:.2f}")
    elif structure == "BEAR" and bear_fvgs:
        fresh = [f for f in bear_fvgs if f["fresh"]]
        if fresh:
            score -= 0.8
            reasons.append(f"Fresh Bearish FVG {fresh[-1]['bottom']:.2f}-{fresh[-1]['top']:.2f}")
        retest = [f for f in bear_fvgs if f["retest"] and not f["fresh"]]
        if retest:
            score -= 0.6
            reasons.append(f"Bearish FVG retest {retest[-1]['bottom']:.2f}-{retest[-1]['top']:.2f}")
        unfilled = [f for f in bear_fvgs if not f["filled"] and current_price < f["bottom"]]
        if unfilled:
            score -= 0.2
            reasons.append(f"Unfilled Bear FVG resistance {unfilled[-1]['bottom']:.2f}-{unfilled[-1]['top']:.2f}")
    else:
        reasons.append(f"Structure {structure} — no matching FVG")
    return float(np.clip(score, -1.0, 1.0)), " | ".join(reasons)


# ─────────────────────────────────────────────
#  SCORING SYSTEM
# ─────────────────────────────────────────────
def calculate_score(tf_results, current_price, weekly_structure):
    points    = 0
    reasons   = []
    direction = None

    w_struct = tf_results.get("1w", {}).get("structure", "RANGE")
    if w_struct == "BULL":
        points    += 3
        direction  = "BUY"
        reasons.append("Weekly BULL (+3)")
    elif w_struct == "BEAR":
        points    += 3
        direction  = "SELL"
        reasons.append("Weekly BEAR (+3)")
    else:
        reasons.append("Weekly RANGE — no trade")
        return 0, "WAIT", reasons

    d_struct = tf_results.get("1d", {}).get("structure", "RANGE")
    if (direction == "BUY"  and d_struct == "BULL") or \
       (direction == "SELL" and d_struct == "BEAR"):
        points  += 2
        reasons.append(f"Daily confirms {direction} (+2)")
    else:
        reasons.append(f"Daily not confirming — {d_struct} (0)")

    h4_struct = tf_results.get("4h", {}).get("structure", "RANGE")
    if (direction == "BUY"  and h4_struct == "BULL") or \
       (direction == "SELL" and h4_struct == "BEAR"):
        points  += 2
        reasons.append(f"4H confirms {direction} (+2)")
    else:
        reasons.append(f"4H not confirming — {h4_struct} (0)")

    fvg_score = tf_results.get("1h", {}).get("fvg_score", 0)
    if (direction == "BUY"  and fvg_score > 0) or \
       (direction == "SELL" and fvg_score < 0):
        points  += 1
        reasons.append(f"FVG confirms {direction} (+1)")
    else:
        reasons.append("FVG not confirming (0)")

    reasons.append(f"Total: {points}/8")
    return points, direction, reasons


# ─────────────────────────────────────────────
#  TIMEFRAME ANALYSIS
# ─────────────────────────────────────────────
def analyze_timeframe(exchange, symbol, tf):
    try:
        limit = 100 if tf == "1w" else 200
        bars  = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    except Exception as e:
        return {"score": 0.0, "fvg_score": 0.0,
                "reasons": [f"{tf}: fetch error"], "error": True, "atr": 0}

    df = pd.DataFrame(bars, columns=["time", "open", "high", "low", "close", "volume"])

    min_bars = 30 if tf == "1w" else 60
    if df.empty or len(df) < min_bars:
        return {"score": 0.0, "fvg_score": 0.0,
                "reasons": [f"{tf}: data kam"], "error": True, "atr": 0}

    df["time"]    = pd.to_datetime(df["time"], unit="ms")
    structure     = detect_structure(df)
    fvgs          = detect_fvg(df, FVG_LOOKBACK, MIN_FVG_SIZE)
    current_price = df["close"].iloc[-1]
    score, reason = fvg_signal(fvgs, structure, current_price)
    bull_c        = len([f for f in fvgs if f["type"] == "BULL"])
    bear_c        = len([f for f in fvgs if f["type"] == "BEAR"])
    near_level, _ = detect_key_levels(df, current_price)
    atr           = calc_atr(df, ATR_PERIOD)

    return {
        "score":      score,
        "fvg_score":  score,
        "reasons":    [f"{tf}: {structure} | {reason}"],
        "structure":  structure,
        "fvg_bull":   bull_c,
        "fvg_bear":   bear_c,
        "near_level": near_level,
        "price":      current_price,
        "atr":        atr,
        "error":      False,
    }


# ─────────────────────────────────────────────
#  FUNDING RATE
# ─────────────────────────────────────────────
def get_funding_rate(exchange, symbol):
    try:
        fr = exchange.fetch_funding_rate(symbol)
        return float(fr.get("fundingRate", 0.0))
    except:
        return 0.0


# ─────────────────────────────────────────────
#  SIGNAL READER
# ─────────────────────────────────────────────
def read_signal():
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        data = {}
        for line in lines:
            if ":" in line:
                key, val = line.split(":", 1)
                data[key.strip()] = val.strip()
        return (
            data.get("SIGNAL", "WAIT"),
            int(data.get("CONFIDENCE", "0")),
            float(data.get("SCORE", "0")),
            data.get("REASON", ""),
        )
    except:
        return "WAIT", 0, 0.0, ""


# ─────────────────────────────────────────────
#  PnL CALCULATOR
# ─────────────────────────────────────────────
def calc_pnl(side, entry, exit_price, pos_size):
    return (exit_price - entry) * pos_size if side == "BUY" \
           else (entry - exit_price) * pos_size


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

            if price == 0:
                time.sleep(UPDATE_INTERVAL)
                continue

            trading = is_trading_hours()

            if position is not None:
                pnl      = calc_pnl(position, entry, price, psize)
                dur      = str(datetime.now() - etime).split(".")[0]
                pnl_icon = "+" if pnl >= 0 else ""

                if position == "BUY":
                    tp_dist = ((tp - price) / price) * 100
                    sl_dist = ((price - sl) / price) * 100
                else:
                    tp_dist = ((price - tp) / price) * 100
                    sl_dist = ((sl - price) / price) * 100

                send_telegram(
                    f"--- TRADE UPDATE ---\n"
                    f"Time         : {now}\n"
                    f"Side         : {position}\n"
                    f"Entry        : {entry:.2f}\n"
                    f"Price        : {price:.2f}\n"
                    f"PnL          : {pnl_icon}{pnl:.2f} USDT\n"
                    f"Capital Used : {capital_used:.2f} USDT\n"
                    f"Capital      : {capital:.2f} USDT\n"
                    f"Duration     : {dur}\n"
                    f"--------------------\n"
                    f"TP           : {tp:.2f} ({tp_dist:.2f}% door)\n"
                    f"SL           : {sl:.2f} ({sl_dist:.2f}% door)\n"
                    f"Score        : {points}/8"
                )
            else:
                session_status = "Active" if trading else f"Band — Next: {next_session_time()}"
                send_telegram(
                    f"--- MARKET UPDATE ---\n"
                    f"Time    : {now}\n"
                    f"Price   : {price:.2f}\n"
                    f"Score   : {points}/8\n"
                    f"Capital : {capital:.2f} USDT\n"
                    f"Session : {session_status}\n"
                    f"Status  : Next entry ka wait...\n"
                    f"---------------------"
                )

        except Exception as e:
            print(f"[UPDATE ERROR] {e}")
        time.sleep(UPDATE_INTERVAL)


# ─────────────────────────────────────────────
#  DAILY REPORT — Raat 11:59 PM
# ─────────────────────────────────────────────
def run_daily_report():
    """Roz raat 11:59 PM par daily report bhejta hai"""
    while True:
        try:
            ist  = timezone(timedelta(hours=5, minutes=30))
            now  = datetime.now(ist)
            h, m = now.hour, now.minute

            # 11:59 PM par report bhejo
            if h == 23 and m == 59:
                daily  = get_daily_stats()
                overall = get_overall_stats()

                if daily:
                    send_telegram(
                        f"--- DAILY REPORT ---\n"
                        f"Date         : {now.strftime('%d/%m/%Y')}\n"
                        f"Total Trades : {daily['total']}\n"
                        f"Win          : {daily['wins']}\n"
                        f"Loss         : {daily['losses']}\n"
                        f"Win Rate     : {daily['win_rate']}%\n"
                        f"Daily PnL    : {daily['pnl']:+.2f} USDT\n"
                        f"Capital      : {daily['capital']:.2f} USDT\n"
                        f"Best Trade   : +{daily['best']:.2f} USDT\n"
                        f"Worst Trade  : {daily['worst']:.2f} USDT\n"
                        f"--------------------\n"
                        f"OVERALL:\n"
                        f"Total Trades : {overall['total']}\n"
                        f"Win Rate     : {overall['win_rate']}%\n"
                        f"Total PnL    : {overall['pnl']:+.2f} USDT\n"
                        f"--------------------"
                    )
                else:
                    send_telegram(
                        f"--- DAILY REPORT ---\n"
                        f"Date  : {now.strftime('%d/%m/%Y')}\n"
                        f"Aaj koi trade nahi hua\n"
                        f"--------------------"
                    )
                time.sleep(70)  # 1 min wait — dobara trigger nahi ho

        except Exception as e:
            print(f"[DAILY REPORT ERROR] {e}")

        time.sleep(30)


# ─────────────────────────────────────────────
#  DECISION ENGINE
# ─────────────────────────────────────────────
def run_decision_engine():
    exchange = get_exchange()
    print("[DECISION] Engine v6.0 started")

    while True:
        try:
            scan_time   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            tf_results  = {}
            all_reasons = []

            for tf in TIMEFRAMES.keys():
                result = analyze_timeframe(exchange, SYMBOL, tf)
                tf_results[tf] = result
                all_reasons.extend(result.get("reasons", []))

            current_price    = tf_results.get("1h", {}).get("price", 0)
            weekly_structure = tf_results.get("1w", {}).get("structure", "RANGE")
            atr_1h           = tf_results.get("1h", {}).get("atr", 0)

            points, direction, score_reasons = calculate_score(
                tf_results, current_price, weekly_structure
            )
            all_reasons.extend(score_reasons)

            funding = get_funding_rate(exchange, SYMBOL)
            if funding > 0.0005:
                all_reasons.append("Funding HIGH -> bearish pressure")
            elif funding < -0.0005:
                all_reasons.append("Funding NEGATIVE -> bullish pressure")

            confidence = int((points / 8) * 100)

            if points >= MIN_SCORE_POINTS and direction == "BUY":
                signal = "BUY"
            elif points >= MIN_SCORE_POINTS and direction == "SELL":
                signal = "SELL"
            else:
                signal = "WAIT"

            print(f"[DECISION] {scan_time} | Points={points}/8 | Conf={confidence}% | {signal} | ATR={atr_1h:.2f}")

            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write(
                    f"SIGNAL:{signal}\n"
                    f"CONFIDENCE:{confidence}\n"
                    f"SCORE:{points}\n"
                    f"ATR:{round(atr_1h, 4)}\n"
                    f"TIME:{scan_time}\n"
                    f"REASON:{' | '.join(all_reasons)}\n"
                )

            update_state(
                last_signal=signal,
                last_conf=confidence,
                last_points=points,
                last_atr=atr_1h,
            )

            entry_log = {
                "time":       scan_time,
                "signal":     signal,
                "confidence": confidence,
                "points":     points,
                "direction":  direction,
                "atr":        round(atr_1h, 4),
                "reasons":    all_reasons,
            }
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    log = json.load(f)
            except:
                log = []
            log.append(entry_log)
            log = log[-500:]
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(log, f, indent=2)

        except Exception as e:
            print(f"[DECISION ERROR] {e}")
            send_telegram(f"DECISION ENGINE ERROR!\n{str(e)[:200]}")
            time.sleep(30)

        time.sleep(DECISION_SCAN)


# ─────────────────────────────────────────────
#  SIGNAL READER — ATR bhi padhta hai
# ─────────────────────────────────────────────
def read_signal_with_atr():
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        data = {}
        for line in lines:
            if ":" in line:
                key, val = line.split(":", 1)
                data[key.strip()] = val.strip()
        return (
            data.get("SIGNAL", "WAIT"),
            int(data.get("CONFIDENCE", "0")),
            float(data.get("SCORE", "0")),
            data.get("REASON", ""),
            float(data.get("ATR", "0")),
        )
    except:
        return "WAIT", 0, 0.0, "", 0.0


# ─────────────────────────────────────────────
#  EXECUTION ENGINE
# ─────────────────────────────────────────────
def run_execution_engine():
    ex           = ccxt.binanceusdm({"enableRateLimit": True})
    capital      = load_capital()
    position     = None
    entry_price  = 0.0
    entry_time   = None
    pos_size     = 0.0
    sl_price     = 0.0
    tp_price     = 0.0
    capital_used = 0.0
    cooldown_end = None

    print("[EXECUTE] Waiting for first decision signal...")
    while True:
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            if "SIGNAL:" in content:
                print("[EXECUTE] Signal found! Starting...")
                break
        except:
            pass
        print("[EXECUTE] No signal yet — waiting 30s...")
        time.sleep(30)

    print("[EXECUTE] Engine started")
    send_telegram(
        f"TRADE BOT v6.0 STARTED\n"
        f"Capital  : {capital:.2f} USDT\n"
        f"Symbol   : {SYMBOL}\n"
        f"Mode     : Paper Trading\n"
        f"Edge     : Weekly+Daily+4H+FVG+ATR\n"
        f"Min Score: {MIN_SCORE_POINTS}/8\n"
        f"Cooldown : {COOLDOWN//60} min\n"
        f"Sessions : London + NY only"
    )

    while True:
        try:
            signal, confidence, score, reason, atr = read_signal_with_atr()
            ticker        = ex.fetch_ticker(SYMBOL)
            current_price = float(ticker["last"])
            now           = datetime.now().strftime("%H:%M:%S")

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

            # Trailing SL
            if position is not None and TRAILING_STOP:
                if position == "BUY":
                    profit_pct = ((current_price - entry_price) / entry_price) * 100
                    if profit_pct >= TRAIL_TRIGGER:
                        new_sl = current_price * (1 - TRAIL_OFFSET / 100)
                        if new_sl > sl_price:
                            sl_price = new_sl
                            update_state(sl_price=sl_price)
                            print(f"[TRAIL] BUY SL -> {sl_price:.2f}")
                elif position == "SELL":
                    profit_pct = ((entry_price - current_price) / entry_price) * 100
                    if profit_pct >= TRAIL_TRIGGER:
                        new_sl = current_price * (1 + TRAIL_OFFSET / 100)
                        if new_sl < sl_price:
                            sl_price = new_sl
                            update_state(sl_price=sl_price)
                            print(f"[TRAIL] SELL SL -> {sl_price:.2f}")

            # SL/TP check
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

                    # Win rate tracker mein save karo
                    save_trade_history(
                        position, entry_price, current_price,
                        pnl, capital, duration, label
                    )

                    print(f"[EXECUTE] {label} | {position} | PnL={pnl:+.2f} | Capital={capital:.2f}")
                    send_telegram(
                        f"TRADE CLOSED — {label}\n"
                        f"Side         : {position}\n"
                        f"Entry        : {entry_price:.2f}\n"
                        f"Exit         : {current_price:.2f}\n"
                        f"PnL          : {pnl:+.2f} USDT\n"
                        f"Capital Used : {capital_used:.2f} USDT\n"
                        f"Capital      : {capital:.2f} USDT\n"
                        f"Time         : {duration}"
                    )

                    position     = None
                    entry_price  = 0.0
                    entry_time   = None
                    pos_size     = 0.0
                    sl_price     = 0.0
                    tp_price     = 0.0
                    capital_used = 0.0
                    cooldown_end = time.time() + COOLDOWN

                    update_state(position=None, capital_used=0.0, capital=capital)
                    print(f"[COOLDOWN] {COOLDOWN//60} min wait...")
                    time.sleep(EXECUTE_SCAN)
                    continue

            # Cooldown check
            if cooldown_end is not None and time.time() < cooldown_end:
                remaining = int((cooldown_end - time.time()) / 60)
                print(f"[{now}] Cooldown — {remaining} min baaki")
                time.sleep(EXECUTE_SCAN)
                continue

            # Market hours check
            if not is_trading_hours():
                print(f"[{now}] Session band — {next_session_time()} tak wait")
                time.sleep(60)
                continue

            # Entry check
            if position is None:
                if signal in ["BUY", "SELL"] and confidence >= MIN_CONFIDENCE:

                    # ATR based SL/TP
                    if atr > 0:
                        sl_dist = atr * ATR_SL_MULT
                        tp_dist = atr * ATR_TP_MULT
                        sl_pct  = (sl_dist / current_price) * 100
                        tp_pct  = (tp_dist / current_price) * 100
                    else:
                        sl_pct = 0.8
                        tp_pct = 0.8

                    risk_amount  = capital * (RISK_PERCENT / 100)
                    capital_used = risk_amount * LEVERAGE
                    pos_size     = capital_used / current_price
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

                    print(f"[EXECUTE] OPENED | {position} | Entry={entry_price:.2f} | SL={sl_price:.2f} | TP={tp_price:.2f} | ATR={atr:.2f}")
                    send_telegram(
                        f"TRADE OPENED\n"
                        f"Side         : {position}\n"
                        f"Entry        : {entry_price:.2f}\n"
                        f"SL           : {sl_price:.2f}\n"
                        f"TP           : {tp_price:.2f}\n"
                        f"ATR          : {atr:.2f}\n"
                        f"Size         : {pos_size:.4f} ETH\n"
                        f"Capital Used : {capital_used:.2f} USDT\n"
                        f"Total Capital: {capital:.2f} USDT\n"
                        f"Conf         : {confidence}%\n"
                        f"Score        : {int(score)}/8\n"
                        f"Reason       : {reason[:300]}"
                    )
                else:
                    print(f"[{now}] Price={current_price:.2f} | WAIT | Score={int(score)}/8")

            # Hold / Flip
            else:
                if (position == "BUY"  and signal == "SELL" and confidence >= MIN_CONFIDENCE) or \
                   (position == "SELL" and signal == "BUY"  and confidence >= MIN_CONFIDENCE):

                    pnl      = calc_pnl(position, entry_price, current_price, pos_size)
                    capital += pnl
                    duration = str(datetime.now() - entry_time).split(".")[0]
                    save_capital(capital)

                    save_trade_history(
                        position, entry_price, current_price,
                        pnl, capital, duration, "Signal Flip"
                    )

                    send_telegram(
                        f"TRADE CLOSED — Signal Flip\n"
                        f"Side         : {position}\n"
                        f"Entry        : {entry_price:.2f}\n"
                        f"Exit         : {current_price:.2f}\n"
                        f"PnL          : {pnl:+.2f} USDT\n"
                        f"Capital Used : {capital_used:.2f} USDT\n"
                        f"Capital      : {capital:.2f} USDT\n"
                        f"Time         : {duration}"
                    )

                    position     = None
                    entry_price  = 0.0
                    entry_time   = None
                    pos_size     = 0.0
                    sl_price     = 0.0
                    tp_price     = 0.0
                    capital_used = 0.0
                    cooldown_end = time.time() + COOLDOWN

                    update_state(position=None, capital_used=0.0, capital=capital)
                    print(f"[COOLDOWN] Signal flip — {COOLDOWN//60} min wait...")

                else:
                    pnl_now = calc_pnl(position, entry_price, current_price, pos_size)
                    print(f"[{now}] Price={current_price:.2f} | Holding {position} | PnL={pnl_now:+.2f} USDT")

        except Exception as e:
            print(f"[EXECUTE ERROR] {e}")
            send_telegram(f"EXECUTE ENGINE ERROR!\n{str(e)[:200]}")
            time.sleep(30)

        time.sleep(EXECUTE_SCAN)


# ─────────────────────────────────────────────
#  START ALL THREADS
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  TRADE BOT v6.0 STARTING...")
    print("  New: Win Tracker + Hours + ATR SL/TP")
    print("=" * 55)

    t1 = threading.Thread(target=run_server)
    t1.daemon = True
    t1.start()

    t2 = threading.Thread(target=run_decision_engine)
    t2.daemon = True
    t2.start()

    t3 = threading.Thread(target=run_execution_engine)
    t3.daemon = True
    t3.start()

    t4 = threading.Thread(target=run_periodic_update)
    t4.daemon = True
    t4.start()

    t5 = threading.Thread(target=run_daily_report)
    t5.daemon = True
    t5.start()

    print("[INFO] All engines started!")
    print("[INFO] Flask       : port 8080")
    print("[INFO] Decision    : har 300s")
    print("[INFO] Execute     : har 10s")
    print("[INFO] Updates     : har 30 min")
    print("[INFO] Daily Report: raat 11:59 PM")

    while True:
        time.sleep(60)
