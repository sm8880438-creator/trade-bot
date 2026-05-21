"""
SCALPING BOT v1.0 — ETH/USDT Perpetual
Strategy : Smart Money Concepts
  → Liquidity Zones
  → Order Blocks
  → Inducement
  → FVG
Timeframes: 15m (direction), 5m (setup), 1m (entry)
"""

import threading
import time
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Scalping Bot Running! v1.0"

def run_server():
    app.run(host='0.0.0.0', port=8081)  # alag port


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
    "15m": "direction",
    "5m":  "setup",
    "1m":  "entry",
}

DECISION_SCAN    = 60            # har 1 min scan
OUTPUT_FILE      = "scalping_output.txt"
LOG_FILE         = "scalping_log.json"
CAPITAL_FILE     = "scalping_capital.txt"
TRADE_HISTORY    = "scalping_history.json"

BOT_TOKEN        = "8161773850:AAFcWw3UnlSe2TrMooB2uvgZQZUqIW0zW2w"
CHAT_ID          = "7102976298"
CAPITAL          = 105.26
RISK_PERCENT     = 5
LEVERAGE         = 5
MIN_CONFIDENCE   = 60            # scalping mein higher confidence
EXECUTE_SCAN     = 5             # har 5 sec price check

TRAILING_STOP    = True
TRAIL_TRIGGER    = 0.3           # scalping mein tight
TRAIL_OFFSET     = 0.2

UPDATE_INTERVAL  = 1800
COOLDOWN         = 300           # 5 min cooldown (scalping)
MIN_SCORE_POINTS = 6

# ATR Settings
ATR_PERIOD       = 14
ATR_SL_MULT      = 1.0           # tight SL scalping ke liye
ATR_TP_MULT      = 2.0           # 1:2 RR

# TP Early Exit
TP_EXIT_MIN_PCT   = 0.65
TP_EXIT_MAX_PCT   = 0.80
TP_HOLD_MIN_SCORE = 6

# Max hold time — scalping
MAX_HOLD_MINUTES  = 30           # 30 min se zyada nahi

# Thread safety
state_lock = threading.Lock()


# ─────────────────────────────────────────────
#  MARKET HOURS
# ─────────────────────────────────────────────
def is_trading_hours():
    ist  = timezone(timedelta(hours=5, minutes=30))
    now  = datetime.now(ist)
    h, m = now.hour, now.minute
    mins = h * 60 + m
    london_start = 12 * 60 + 30
    london_end   = 20 * 60 + 30
    ny_start     = 18 * 60 + 30
    in_london    = london_start <= mins <= london_end
    in_ny        = mins >= ny_start or mins <= 30
    return in_london or in_ny

def next_session_time():
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
#  ATR
# ─────────────────────────────────────────────
def calc_atr(df, period=14):
    high  = df["high"]
    low   = df["low"]
    close = df["close"]
    tr1   = high - low
    tr2   = (high - close.shift(1)).abs()
    tr3   = (low  - close.shift(1)).abs()
    tr    = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr   = tr.ewm(span=period, adjust=False).mean()
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
#  TRADE HISTORY
# ─────────────────────────────────────────────
def save_trade_history(side, entry, exit_price, pnl, capital, duration, label):
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
    try:
        with open(TRADE_HISTORY, "r", encoding="utf-8") as f:
            history = json.load(f)
    except:
        return None

    today  = datetime.now().strftime("%d/%m/%Y")
    trades = [t for t in history if t["date"] == today]
    if not trades:
        return None

    total     = len(trades)
    wins      = len([t for t in trades if t["result"] == "WIN"])
    losses    = total - wins
    win_rate  = round((wins / total) * 100, 1) if total > 0 else 0
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
    try:
        with open(TRADE_HISTORY, "r", encoding="utf-8") as f:
            history = json.load(f)
    except:
        return None

    if not history:
        return None

    total     = len(history)
    wins      = len([t for t in history if t["result"] == "WIN"])
    losses    = total - wins
    win_rate  = round((wins / total) * 100, 1) if total > 0 else 0
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
            r = requests.post(
                url,
                data={"chat_id": CHAT_ID, "text": f"[SCALP] {message}"},
                timeout=15
            )
            if r.status_code == 200:
                return
        except Exception as e:
            print(f"[WARN] Telegram attempt {attempt+1}/3: {e}")
            time.sleep(3)


# ─────────────────────────────────────────────
#  MARKET STRUCTURE
# ─────────────────────────────────────────────
def detect_structure(df, swing_bars=3):   # scalping mein 3 bars
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
#  LIQUIDITY ZONES
# ─────────────────────────────────────────────
def detect_liquidity(df, lookback=50):
    """
    Liquidity zones detect karta hai:
    - Buy side liquidity: Previous highs ke upar
    - Sell side liquidity: Previous lows ke neeche
    """
    recent        = df.tail(lookback)
    highs         = recent["high"].values
    lows          = recent["low"].values
    current_price = df["close"].iloc[-1]
    n             = len(highs)
    swing_bars    = 3

    buy_liquidity  = []   # Highs ke upar — retail stops
    sell_liquidity = []   # Lows ke neeche — retail stops

    for i in range(swing_bars, n - swing_bars):
        if highs[i] == max(highs[i - swing_bars: i + swing_bars + 1]):
            buy_liquidity.append(highs[i])
        if lows[i] == min(lows[i - swing_bars: i + swing_bars + 1]):
            sell_liquidity.append(lows[i])

    # Kya price ne recently liquidity sweep kiya?
    # Buy side swept: price ne high ke upar gaya phir wapas aaya
    buy_swept  = False
    sell_swept = False

    if len(buy_liquidity) >= 1:
        last_high = buy_liquidity[-1]
        # Last 3 candles mein high ke upar gaya aur wapas aaya
        recent_3 = df.tail(3)
        if any(recent_3["high"] > last_high) and current_price < last_high:
            buy_swept = True

    if len(sell_liquidity) >= 1:
        last_low = sell_liquidity[-1]
        recent_3 = df.tail(3)
        if any(recent_3["low"] < last_low) and current_price > last_low:
            sell_swept = True

    return {
        "buy_liquidity":  buy_liquidity[-3:] if buy_liquidity else [],
        "sell_liquidity": sell_liquidity[-3:] if sell_liquidity else [],
        "buy_swept":      buy_swept,
        "sell_swept":     sell_swept,
    }


# ─────────────────────────────────────────────
#  ORDER BLOCKS
# ─────────────────────────────────────────────
def detect_order_blocks(df, lookback=30):
    """
    Order Blocks detect karta hai:
    - Bearish OB: Last bullish candle before big drop
    - Bullish OB: Last bearish candle before big pump
    """
    recent        = df.tail(lookback).reset_index(drop=True)
    n             = len(recent)
    current_price = recent["close"].iloc[-1]

    bullish_obs = []
    bearish_obs = []

    for i in range(1, n - 1):
        curr  = recent.iloc[i]
        next_ = recent.iloc[i + 1]

        # Bearish OB — bullish candle phir big bearish move
        if (curr["close"] > curr["open"] and          # bullish candle
                next_["close"] < next_["open"] and    # next bearish
                (next_["open"] - next_["close"]) >    # big move
                (curr["close"] - curr["open"]) * 1.5):
            bearish_obs.append({
                "top":    round(curr["high"], 4),
                "bottom": round(curr["open"], 4),
                "mid":    round((curr["high"] + curr["open"]) / 2, 4),
                "idx":    i,
                # Price OB zone mein hai?
                "price_in_ob": curr["open"] <= current_price <= curr["high"],
            })

        # Bullish OB — bearish candle phir big bullish move
        if (curr["close"] < curr["open"] and          # bearish candle
                next_["close"] > next_["open"] and    # next bullish
                (next_["close"] - next_["open"]) >    # big move
                (curr["open"] - curr["close"]) * 1.5):
            bullish_obs.append({
                "top":    round(curr["open"], 4),
                "bottom": round(curr["low"], 4),
                "mid":    round((curr["open"] + curr["low"]) / 2, 4),
                "idx":    i,
                "price_in_ob": curr["low"] <= current_price <= curr["open"],
            })

    return {
        "bullish_obs": bullish_obs[-3:] if bullish_obs else [],
        "bearish_obs": bearish_obs[-3:] if bearish_obs else [],
    }


# ─────────────────────────────────────────────
#  INDUCEMENT DETECTION
# ─────────────────────────────────────────────
def detect_inducement(df, lookback=20):
    """
    Inducement detect karta hai:
    Fake breakout jo retail traders ko trap karta hai
    Phir real move hota hai opposite direction mein
    """
    recent        = df.tail(lookback).reset_index(drop=True)
    n             = len(recent)
    current_price = recent["close"].iloc[-1]

    bull_inducement = False
    bear_inducement = False

    for i in range(2, n - 1):
        prev  = recent.iloc[i - 1]
        curr  = recent.iloc[i]
        next_ = recent.iloc[i + 1]

        # Bearish inducement:
        # Price ne high ke upar gaya (fake breakout)
        # Phir strong neeche aaya
        if (curr["high"] > prev["high"] and      # fake breakout upar
                curr["close"] < prev["high"] and  # close wapas neeche
                next_["close"] < curr["low"]):    # confirmation neeche
            bear_inducement = True

        # Bullish inducement:
        # Price ne low ke neeche gaya (fake breakdown)
        # Phir strong upar aaya
        if (curr["low"] < prev["low"] and        # fake breakdown neeche
                curr["close"] > prev["low"] and   # close wapas upar
                next_["close"] > curr["high"]):   # confirmation upar
            bull_inducement = True

    return {
        "bull_inducement": bull_inducement,
        "bear_inducement": bear_inducement,
    }


# ─────────────────────────────────────────────
#  FVG DETECTION
# ─────────────────────────────────────────────
def detect_fvg(df, lookback=30, min_gap_pct=0.05):
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
                    "fresh":  (i >= n - 5),
                    "retest": (current_price >= gap_bottom * 0.998 and
                               current_price <= gap_top * 1.002),
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
                    "fresh":  (i >= n - 5),
                    "retest": (current_price >= gap_bottom * 0.998 and
                               current_price <= gap_top * 1.002),
                })

    return fvgs


# ─────────────────────────────────────────────
#  SMART MONEY SCORING — 8 Points
# ─────────────────────────────────────────────
def smart_money_score(structure_15m, structure_5m, liq, obs, ind, fvgs, current_price):
    """
    8 point Smart Money scoring:
    15m Structure  → +2
    Order Block    → +2
    Liquidity Swept→ +2
    FVG present    → +1
    Inducement     → +1
    """
    points    = 0
    direction = None
    reasons   = []

    # 1. 15m Structure — Direction decide (2 points)
    if structure_15m == "BULL":
        points    += 2
        direction  = "BUY"
        reasons.append("15m BULL structure (+2)")
    elif structure_15m == "BEAR":
        points    += 2
        direction  = "SELL"
        reasons.append("15m BEAR structure (+2)")
    else:
        reasons.append("15m RANGE — no trade")
        return 0, "WAIT", reasons

    # 2. Order Block hit (2 points)
    if direction == "BUY":
        bull_obs_hit = [ob for ob in obs["bullish_obs"] if ob["price_in_ob"]]
        if bull_obs_hit:
            points  += 2
            reasons.append(f"Bullish OB hit {bull_obs_hit[-1]['bottom']:.2f}-{bull_obs_hit[-1]['top']:.2f} (+2)")
        else:
            reasons.append("No Bullish OB hit (0)")
    else:
        bear_obs_hit = [ob for ob in obs["bearish_obs"] if ob["price_in_ob"]]
        if bear_obs_hit:
            points  += 2
            reasons.append(f"Bearish OB hit {bear_obs_hit[-1]['bottom']:.2f}-{bear_obs_hit[-1]['top']:.2f} (+2)")
        else:
            reasons.append("No Bearish OB hit (0)")

    # 3. Liquidity Swept (2 points)
    if direction == "BUY" and liq["sell_swept"]:
        points  += 2
        reasons.append("Sell side liquidity swept (+2)")
    elif direction == "SELL" and liq["buy_swept"]:
        points  += 2
        reasons.append("Buy side liquidity swept (+2)")
    else:
        reasons.append("No liquidity sweep (0)")

    # 4. FVG present (1 point)
    bull_fvgs = [f for f in fvgs if f["type"] == "BULL" and f["retest"]]
    bear_fvgs = [f for f in fvgs if f["type"] == "BEAR" and f["retest"]]

    if direction == "BUY" and bull_fvgs:
        points  += 1
        reasons.append(f"Bullish FVG retest {bull_fvgs[-1]['bottom']:.2f}-{bull_fvgs[-1]['top']:.2f} (+1)")
    elif direction == "SELL" and bear_fvgs:
        points  += 1
        reasons.append(f"Bearish FVG retest {bear_fvgs[-1]['bottom']:.2f}-{bear_fvgs[-1]['top']:.2f} (+1)")
    else:
        reasons.append("No FVG retest (0)")

    # 5. Inducement (1 point)
    if direction == "BUY" and ind["bull_inducement"]:
        points  += 1
        reasons.append("Bullish inducement detected (+1)")
    elif direction == "SELL" and ind["bear_inducement"]:
        points  += 1
        reasons.append("Bearish inducement detected (+1)")
    else:
        reasons.append("No inducement (0)")

    reasons.append(f"Total: {points}/8")
    return points, direction, reasons


# ─────────────────────────────────────────────
#  TIMEFRAME FETCH
# ─────────────────────────────────────────────
def fetch_tf_data(exchange, symbol, tf, limit=100):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
        df   = pd.DataFrame(bars, columns=["time", "open", "high", "low", "close", "volume"])
        if df.empty or len(df) < 20:
            return None
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        return df
    except Exception as e:
        print(f"[ERROR] {tf} fetch fail: {e}")
        return None


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
            float(data.get("ATR", "0")),
        )
    except:
        return "WAIT", 0, 0.0, "", 0.0


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
    "last_tp_zone": "",
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

                tp_zone_line = f"\nTP Zone      : {tp_zone}" if tp_zone else ""

                send_telegram(
                    f"--- SCALP TRADE UPDATE ---\n"
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
                    f"{tp_zone_line}"
                )
            else:
                session_status = "Active" if trading else f"Band — Next: {next_session_time()}"
                send_telegram(
                    f"--- SCALP MARKET UPDATE ---\n"
                    f"Time    : {now}\n"
                    f"Price   : {price:.2f}\n"
                    f"Score   : {points}/8\n"
                    f"Capital : {capital:.2f} USDT\n"
                    f"Session : {session_status}\n"
                    f"Status  : Next scalp entry ka wait...\n"
                    f"---------------------------"
                )

        except Exception as e:
            print(f"[UPDATE ERROR] {e}")
        time.sleep(UPDATE_INTERVAL)


# ─────────────────────────────────────────────
#  DAILY REPORT
# ─────────────────────────────────────────────
def run_daily_report():
    while True:
        try:
            ist  = timezone(timedelta(hours=5, minutes=30))
            now  = datetime.now(ist)
            h, m = now.hour, now.minute

            if h == 23 and m == 59:
                daily   = get_daily_stats()
                overall = get_overall_stats()

                if daily:
                    send_telegram(
                        f"--- SCALP DAILY REPORT ---\n"
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
                        f"--- SCALP DAILY REPORT ---\n"
                        f"Date  : {now.strftime('%d/%m/%Y')}\n"
                        f"Aaj koi scalp trade nahi hua\n"
                        f"--------------------"
                    )
                time.sleep(70)

        except Exception as e:
            print(f"[DAILY REPORT ERROR] {e}")
        time.sleep(30)


# ─────────────────────────────────────────────
#  DECISION ENGINE
# ─────────────────────────────────────────────
def run_decision_engine():
    exchange = get_exchange()
    print("[SCALP DECISION] Engine v1.0 started")

    while True:
        try:
            scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Fetch timeframes
            df_15m = fetch_tf_data(exchange, SYMBOL, "15m", 100)
            df_5m  = fetch_tf_data(exchange, SYMBOL, "5m",  100)
            df_1m  = fetch_tf_data(exchange, SYMBOL, "1m",  100)

            if df_15m is None or df_5m is None or df_1m is None:
                print(f"[DECISION] Data fetch fail — retry")
                time.sleep(30)
                continue

            current_price = df_1m["close"].iloc[-1]
            atr_1m        = calc_atr(df_1m, ATR_PERIOD)

            # Analysis
            structure_15m = detect_structure(df_15m, swing_bars=3)
            structure_5m  = detect_structure(df_5m,  swing_bars=3)
            liq           = detect_liquidity(df_5m,  lookback=50)
            obs           = detect_order_blocks(df_5m, lookback=30)
            ind           = detect_inducement(df_1m,  lookback=20)
            fvgs          = detect_fvg(df_1m,         lookback=30)

            # Smart Money Score
            points, direction, reasons = smart_money_score(
                structure_15m, structure_5m,
                liq, obs, ind, fvgs, current_price
            )

            confidence = int((points / 8) * 100)

            if points >= MIN_SCORE_POINTS and direction == "BUY":
                signal = "BUY"
            elif points >= MIN_SCORE_POINTS and direction == "SELL":
                signal = "SELL"
            else:
                signal = "WAIT"

            print(f"[SCALP] {scan_time} | Points={points}/8 | {signal} | ATR={atr_1m:.2f} | Price={current_price:.2f}")

            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write(
                    f"SIGNAL:{signal}\n"
                    f"CONFIDENCE:{confidence}\n"
                    f"SCORE:{points}\n"
                    f"ATR:{round(atr_1m, 4)}\n"
                    f"TIME:{scan_time}\n"
                    f"REASON:{' | '.join(reasons)}\n"
                )

            update_state(
                last_signal=signal,
                last_conf=confidence,
                last_points=points,
                last_atr=atr_1m,
                last_price=current_price,
            )

            entry_log = {
                "time":       scan_time,
                "signal":     signal,
                "confidence": confidence,
                "points":     points,
                "atr":        round(atr_1m, 4),
                "reasons":    reasons,
            }
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    log = json.load(f)
            except:
                log = []
            log.append(entry_log)
            log = log[-1000:]
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(log, f, indent=2)

        except Exception as e:
            print(f"[DECISION ERROR] {e}")
            send_telegram(f"SCALP DECISION ERROR!\n{str(e)[:200]}")
            time.sleep(30)

        time.sleep(DECISION_SCAN)


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

    print("[SCALP EXECUTE] Waiting for first signal...")
    while True:
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            if "SIGNAL:" in content:
                print("[SCALP EXECUTE] Signal found! Starting...")
                break
        except:
            pass
        time.sleep(10)

    print("[SCALP EXECUTE] Engine started")
    send_telegram(
        f"SCALPING BOT v1.0 STARTED\n"
        f"Capital  : {capital:.2f} USDT\n"
        f"Symbol   : {SYMBOL}\n"
        f"Mode     : Paper Trading\n"
        f"Edge     : Liquidity+OB+FVG+Inducement\n"
        f"Min Score: {MIN_SCORE_POINTS}/8\n"
        f"Cooldown : {COOLDOWN//60} min\n"
        f"Max Hold : {MAX_HOLD_MINUTES} min\n"
        f"Sessions : London + NY only"
    )

    while True:
        try:
            signal, confidence, score, reason, atr = read_signal()
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

            # Max hold time check — scalping
            if position is not None and entry_time is not None:
                held_minutes = (datetime.now() - entry_time).seconds / 60
                if held_minutes >= MAX_HOLD_MINUTES:
                    pnl      = calc_pnl(position, entry_price, current_price, pos_size)
                    capital += pnl
                    duration = str(datetime.now() - entry_time).split(".")[0]
                    save_capital(capital)
                    save_trade_history(
                        position, entry_price, current_price,
                        pnl, capital, duration, "Max Hold Time"
                    )
                    print(f"[MAX HOLD] {MAX_HOLD_MINUTES} min — force exit | PnL={pnl:+.2f}")
                    send_telegram(
                        f"SCALP CLOSED — Max Hold Time\n"
                        f"Side         : {position}\n"
                        f"Entry        : {entry_price:.2f}\n"
                        f"Exit         : {current_price:.2f}\n"
                        f"PnL          : {pnl:+.2f} USDT\n"
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
                    time.sleep(EXECUTE_SCAN)
                    continue

            # TP Zone Check
            if position is not None:
                if position == "BUY":
                    tp_progress = (current_price - entry_price) / (tp_price - entry_price) \
                                  if tp_price != entry_price else 0
                else:
                    tp_progress = (entry_price - current_price) / (entry_price - tp_price) \
                                  if tp_price != entry_price else 0

                if TP_EXIT_MIN_PCT <= tp_progress <= TP_EXIT_MAX_PCT:
                    points = get_state("last_points")
                    if points < TP_HOLD_MIN_SCORE:
                        pnl      = calc_pnl(position, entry_price, current_price, pos_size)
                        capital += pnl
                        duration = str(datetime.now() - entry_time).split(".")[0]
                        save_capital(capital)
                        save_trade_history(
                            position, entry_price, current_price,
                            pnl, capital, duration, "Early Exit — Signal Weak"
                        )
                        print(f"[EARLY EXIT] TP {tp_progress*100:.0f}% | Score={points}/8 | PnL={pnl:+.2f}")
                        update_state(
                            last_tp_zone=f"TP {tp_progress*100:.0f}% par exit | Score={points}/8 | PnL={pnl:+.2f}",
                        )
                        send_telegram(
                            f"SCALP EARLY EXIT\n"
                            f"Side     : {position}\n"
                            f"Entry    : {entry_price:.2f}\n"
                            f"Exit     : {current_price:.2f}\n"
                            f"PnL      : {pnl:+.2f} USDT\n"
                            f"TP Zone  : {tp_progress*100:.0f}%\n"
                            f"Score    : {points}/8 weak"
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
                        time.sleep(EXECUTE_SCAN)
                        continue
                    else:
                        update_state(
                            last_tp_zone=f"TP {tp_progress*100:.0f}% zone | Score={points}/8 strong",
                        )
                else:
                    update_state(last_tp_zone="")

            # Trailing SL
            if position is not None and TRAILING_STOP:
                if position == "BUY":
                    profit_pct = ((current_price - entry_price) / entry_price) * 100
                    if profit_pct >= TRAIL_TRIGGER:
                        new_sl = current_price * (1 - TRAIL_OFFSET / 100)
                        if new_sl > sl_price:
                            sl_price = new_sl
                            update_state(sl_price=sl_price)
                elif position == "SELL":
                    profit_pct = ((entry_price - current_price) / entry_price) * 100
                    if profit_pct >= TRAIL_TRIGGER:
                        new_sl = current_price * (1 + TRAIL_OFFSET / 100)
                        if new_sl < sl_price:
                            sl_price = new_sl
                            update_state(sl_price=sl_price)

            # SL/TP Check
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
                    save_trade_history(
                        position, entry_price, current_price,
                        pnl, capital, duration, label
                    )
                    print(f"[SCALP] {label} | {position} | PnL={pnl:+.2f} | Capital={capital:.2f}")
                    send_telegram(
                        f"SCALP CLOSED — {label}\n"
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
                    update_state(
                        position=None,
                        capital_used=0.0,
                        capital=capital,
                        last_tp_zone="",
                    )
                    time.sleep(EXECUTE_SCAN)
                    continue

            # Cooldown
            if cooldown_end is not None and time.time() < cooldown_end:
                remaining = int((cooldown_end - time.time()) / 60)
                print(f"[{now}] Cooldown — {remaining} min baaki")
                time.sleep(EXECUTE_SCAN)
                continue

            # Market Hours
            if not is_trading_hours():
                print(f"[{now}] Session band")
                time.sleep(60)
                continue

            # Entry
            if position is None:
                if signal in ["BUY", "SELL"] and confidence >= MIN_CONFIDENCE:
                    if atr > 0:
                        sl_pct = (atr * ATR_SL_MULT / current_price) * 100
                        tp_pct = (atr * ATR_TP_MULT / current_price) * 100
                    else:
                        sl_pct = 0.5
                        tp_pct = 1.0

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

                    print(f"[SCALP] OPENED | {position} | Entry={entry_price:.2f} | SL={sl_price:.2f} | TP={tp_price:.2f}")
                    send_telegram(
                        f"SCALP OPENED\n"
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

            # Hold
            else:
                pnl_now = calc_pnl(position, entry_price, current_price, pos_size)
                print(f"[{now}] Price={current_price:.2f} | Holding {position} | PnL={pnl_now:+.2f}")

        except Exception as e:
            print(f"[SCALP EXECUTE ERROR] {e}")
            send_telegram(f"SCALP EXECUTE ERROR!\n{str(e)[:200]}")
            time.sleep(30)

        time.sleep(EXECUTE_SCAN)


# ─────────────────────────────────────────────
#  START ALL THREADS
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  SCALPING BOT v1.0 STARTING...")
    print("  Strategy: Smart Money Concepts")
    print("  Liquidity + Order Blocks + FVG")
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
    print("[INFO] Flask       : port 8081")
    print("[INFO] Decision    : har 60s")
    print("[INFO] Execute     : har 5s")
    print("[INFO] Updates     : har 30 min")
    print("[INFO] Max Hold    : 30 min")

    while True:
        time.sleep(60)
