"""
SCALPING BOT v2.0 — High Frequency
Target: Chote profits, zyada trades
"""

import threading
import time
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Scalping Bot Running! v2.0"

def run_server():
    app.run(host='0.0.0.0', port=8081)


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

BOT_TOKEN        = "8161773850:AAFcWw3UnlSe2TrMooB2uvgZQZUqIW0zW2w"
CHAT_ID          = "7102976298"

CAPITAL          = 100       # 100 USDT
RISK_PERCENT     = 10        # 10% per trade
LEVERAGE         = 10        # 10x leverage
MIN_CONFIDENCE   = 55        # minimum confidence
EXECUTE_SCAN     = 2         # har 2 sec price check
DECISION_SCAN    = 30        # har 30 sec signal scan
COOLDOWN         = 60        # 1 min cooldown
MAX_HOLD_MINUTES = 5         # max 5 min hold

# ATR based SL/TP — tight
ATR_PERIOD       = 7         # fast ATR
ATR_SL_MULT      = 0.5       # tight SL
ATR_TP_MULT      = 1.0       # quick TP

# TP Early Exit
TP_EXIT_MIN_PCT   = 0.65
TP_EXIT_MAX_PCT   = 0.80
TP_HOLD_MIN_SCORE = 5

MIN_SCORE_POINTS  = 5        # 5/8 kaafi hai scalping mein
UPDATE_INTERVAL   = 1800

OUTPUT_FILE      = "scalping_output.txt"
LOG_FILE         = "scalping_log.json"
CAPITAL_FILE     = "scalping_capital.txt"
TRADE_HISTORY    = "scalping_history.json"

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
    mins = now.hour * 60 + now.minute
    if mins < 12 * 60 + 30:
        return "12:30 PM IST"
    elif mins < 18 * 60 + 30:
        return "06:30 PM IST"
    else:
        return "12:30 PM IST kal"


# ─────────────────────────────────────────────
#  ATR
# ─────────────────────────────────────────────
def calc_atr(df, period=7):
    high  = df["high"]
    low   = df["low"]
    close = df["close"]
    tr1   = high - low
    tr2   = (high - close.shift(1)).abs()
    tr3   = (low  - close.shift(1)).abs()
    tr    = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean().iloc[-1]


# ─────────────────────────────────────────────
#  CAPITAL
# ─────────────────────────────────────────────
def load_capital():
    try:
        with open(CAPITAL_FILE, "r") as f:
            cap = float(f.read().strip())
            print(f"[CAPITAL] Loaded: {cap} USDT")
            return cap
    except:
        return CAPITAL

def save_capital(capital):
    try:
        with open(CAPITAL_FILE, "w") as f:
            f.write(str(round(capital, 4)))
    except:
        pass


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
        history.append({
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
        })
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
    return {
        "total": total, "wins": wins, "losses": losses,
        "win_rate": win_rate, "pnl": daily_pnl,
        "best": best, "worst": worst,
        "capital": trades[-1]["capital"],
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
    return {
        "total": total, "wins": wins, "losses": losses,
        "win_rate": win_rate, "pnl": total_pnl,
        "best": round(max(t["pnl"] for t in history), 2),
        "worst": round(min(t["pnl"] for t in history), 2),
        "capital": history[-1]["capital"],
    }


# ─────────────────────────────────────────────
#  EXCHANGE
# ─────────────────────────────────────────────
def get_exchange():
    ex = ccxt.binanceusdm({
        "apiKey": API_KEY, "secret": API_SECRET,
        "enableRateLimit": True,
    })
    ex.load_markets()
    print("[INFO] Binance connected")
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
            print(f"[WARN] Telegram {attempt+1}/3: {e}")
            time.sleep(3)


# ─────────────────────────────────────────────
#  MARKET STRUCTURE
# ─────────────────────────────────────────────
def detect_structure(df, swing_bars=3):
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
#  LIQUIDITY
# ─────────────────────────────────────────────
def detect_liquidity(df, lookback=30):
    recent        = df.tail(lookback)
    highs         = recent["high"].values
    lows          = recent["low"].values
    current_price = df["close"].iloc[-1]
    n             = len(highs)
    swing_bars    = 3
    buy_liquidity  = []
    sell_liquidity = []
    for i in range(swing_bars, n - swing_bars):
        if highs[i] == max(highs[i - swing_bars: i + swing_bars + 1]):
            buy_liquidity.append(highs[i])
        if lows[i] == min(lows[i - swing_bars: i + swing_bars + 1]):
            sell_liquidity.append(lows[i])
    buy_swept  = False
    sell_swept = False
    if buy_liquidity:
        last_high = buy_liquidity[-1]
        recent_3  = df.tail(3)
        if any(recent_3["high"] > last_high) and current_price < last_high:
            buy_swept = True
    if sell_liquidity:
        last_low = sell_liquidity[-1]
        recent_3 = df.tail(3)
        if any(recent_3["low"] < last_low) and current_price > last_low:
            sell_swept = True
    return {"buy_swept": buy_swept, "sell_swept": sell_swept}


# ─────────────────────────────────────────────
#  ORDER BLOCKS
# ─────────────────────────────────────────────
def detect_order_blocks(df, lookback=20):
    recent        = df.tail(lookback).reset_index(drop=True)
    n             = len(recent)
    current_price = recent["close"].iloc[-1]
    bullish_obs   = []
    bearish_obs   = []
    for i in range(1, n - 1):
        curr  = recent.iloc[i]
        next_ = recent.iloc[i + 1]
        if (curr["close"] > curr["open"] and
                next_["close"] < next_["open"] and
                (next_["open"] - next_["close"]) > (curr["close"] - curr["open"]) * 1.5):
            bearish_obs.append({
                "top":        round(curr["high"], 4),
                "bottom":     round(curr["open"], 4),
                "price_in_ob": curr["open"] <= current_price <= curr["high"],
            })
        if (curr["close"] < curr["open"] and
                next_["close"] > next_["open"] and
                (next_["close"] - next_["open"]) > (curr["open"] - curr["close"]) * 1.5):
            bullish_obs.append({
                "top":        round(curr["open"], 4),
                "bottom":     round(curr["low"], 4),
                "price_in_ob": curr["low"] <= current_price <= curr["open"],
            })
    return {
        "bullish_obs": bullish_obs[-3:],
        "bearish_obs": bearish_obs[-3:],
    }


# ─────────────────────────────────────────────
#  FVG
# ─────────────────────────────────────────────
def detect_fvg(df, lookback=20):
    fvgs          = []
    recent        = df.tail(lookback).reset_index(drop=True)
    n             = len(recent)
    current_price = recent["close"].iloc[-1]
    for i in range(2, n):
        c1 = recent.iloc[i - 2]
        c3 = recent.iloc[i]
        if c1["high"] < c3["low"]:
            gap_size = ((c3["low"] - c1["high"]) / c1["high"]) * 100
            if gap_size >= 0.03:
                fvgs.append({
                    "type":   "BULL",
                    "top":    round(c3["low"], 4),
                    "bottom": round(c1["high"], 4),
                    "retest": (current_price >= c1["high"] * 0.999 and
                               current_price <= c3["low"] * 1.001),
                })
        elif c1["low"] > c3["high"]:
            gap_size = ((c1["low"] - c3["high"]) / c3["high"]) * 100
            if gap_size >= 0.03:
                fvgs.append({
                    "type":   "BEAR",
                    "top":    round(c1["low"], 4),
                    "bottom": round(c3["high"], 4),
                    "retest": (current_price >= c3["high"] * 0.999 and
                               current_price <= c1["low"] * 1.001),
                })
    return fvgs


# ─────────────────────────────────────────────
#  SMART MONEY SCORE
# ─────────────────────────────────────────────
def smart_money_score(structure_5m, structure_1m, liq, obs, fvgs):
    points    = 0
    direction = None
    reasons   = []

    # 1. 5m Structure (2 points)
    if structure_5m == "BULL":
        points += 2; direction = "BUY"
        reasons.append("5m BULL (+2)")
    elif structure_5m == "BEAR":
        points += 2; direction = "SELL"
        reasons.append("5m BEAR (+2)")
    else:
        reasons.append("5m RANGE — no trade")
        return 0, "WAIT", reasons

    # 2. 1m Structure confirm (1 point)
    if (direction == "BUY"  and structure_1m == "BULL") or \
       (direction == "SELL" and structure_1m == "BEAR"):
        points += 1
        reasons.append(f"1m confirms {direction} (+1)")
    else:
        reasons.append(f"1m not confirming (0)")

    # 3. Order Block (2 points)
    if direction == "BUY":
        ob_hit = [ob for ob in obs["bullish_obs"] if ob["price_in_ob"]]
        if ob_hit:
            points += 2
            reasons.append(f"Bullish OB hit (+2)")
        else:
            reasons.append("No Bullish OB (0)")
    else:
        ob_hit = [ob for ob in obs["bearish_obs"] if ob["price_in_ob"]]
        if ob_hit:
            points += 2
            reasons.append(f"Bearish OB hit (+2)")
        else:
            reasons.append("No Bearish OB (0)")

    # 4. Liquidity Swept (2 points)
    if direction == "BUY" and liq["sell_swept"]:
        points += 2
        reasons.append("Sell liquidity swept (+2)")
    elif direction == "SELL" and liq["buy_swept"]:
        points += 2
        reasons.append("Buy liquidity swept (+2)")
    else:
        reasons.append("No liquidity sweep (0)")

    # 5. FVG (1 point)
    bull_fvg = [f for f in fvgs if f["type"] == "BULL" and f["retest"]]
    bear_fvg = [f for f in fvgs if f["type"] == "BEAR" and f["retest"]]
    if direction == "BUY" and bull_fvg:
        points += 1
        reasons.append(f"Bull FVG retest (+1)")
    elif direction == "SELL" and bear_fvg:
        points += 1
        reasons.append(f"Bear FVG retest (+1)")
    else:
        reasons.append("No FVG retest (0)")

    reasons.append(f"Total: {points}/8")
    return points, direction, reasons


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
                tp_zone_line = f"\nTP Zone : {tp_zone}" if tp_zone else ""
                send_telegram(
                    f"--- SCALP UPDATE ---\n"
                    f"Time    : {now}\n"
                    f"Side    : {position}\n"
                    f"Entry   : {entry:.2f}\n"
                    f"Price   : {price:.2f}\n"
                    f"PnL     : {pnl_icon}{pnl:.2f} USDT\n"
                    f"Capital : {capital:.2f} USDT\n"
                    f"Duration: {dur}\n"
                    f"--------------------\n"
                    f"TP      : {tp:.2f} ({tp_dist:.2f}% door)\n"
                    f"SL      : {sl:.2f} ({sl_dist:.2f}% door)\n"
                    f"Score   : {points}/8"
                    f"{tp_zone_line}"
                )
            else:
                session = "Active" if is_trading_hours() else f"Band — {next_session_time()}"
                send_telegram(
                    f"--- SCALP MARKET ---\n"
                    f"Time    : {now}\n"
                    f"Price   : {price:.2f}\n"
                    f"Score   : {points}/8\n"
                    f"Capital : {capital:.2f} USDT\n"
                    f"Session : {session}\n"
                    f"Status  : Next scalp ka wait...\n"
                    f"--------------------"
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
            if now.hour == 23 and now.minute == 59:
                daily   = get_daily_stats()
                overall = get_overall_stats()
                if daily:
                    send_telegram(
                        f"--- SCALP DAILY ---\n"
                        f"Date     : {now.strftime('%d/%m/%Y')}\n"
                        f"Trades   : {daily['total']}\n"
                        f"Win      : {daily['wins']}\n"
                        f"Loss     : {daily['losses']}\n"
                        f"Win Rate : {daily['win_rate']}%\n"
                        f"PnL      : {daily['pnl']:+.2f} USDT\n"
                        f"Capital  : {daily['capital']:.2f} USDT\n"
                        f"Best     : +{daily['best']:.2f} USDT\n"
                        f"Worst    : {daily['worst']:.2f} USDT\n"
                        f"--------------------\n"
                        f"OVERALL:\n"
                        f"Trades   : {overall['total']}\n"
                        f"Win Rate : {overall['win_rate']}%\n"
                        f"Total PnL: {overall['pnl']:+.2f} USDT\n"
                        f"--------------------"
                    )
                else:
                    send_telegram(
                        f"--- SCALP DAILY ---\n"
                        f"Aaj koi scalp trade nahi hua\n"
                        f"--------------------"
                    )
                time.sleep(70)
        except Exception as e:
            print(f"[DAILY ERROR] {e}")
        time.sleep(30)


# ─────────────────────────────────────────────
#  PnL CALCULATOR
# ─────────────────────────────────────────────
def calc_pnl(side, entry, exit_price, pos_size):
    return (exit_price - entry) * pos_size if side == "BUY" \
           else (entry - exit_price) * pos_size


# ─────────────────────────────────────────────
#  DECISION ENGINE
# ─────────────────────────────────────────────
def run_decision_engine():
    exchange = get_exchange()
    print("[SCALP DECISION] v2.0 started")

    while True:
        try:
            scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            df_5m = None
            df_1m = None
            try:
                bars_5m = exchange.fetch_ohlcv(SYMBOL, "5m",  limit=100)
                bars_1m = exchange.fetch_ohlcv(SYMBOL, "1m",  limit=100)
                df_5m   = pd.DataFrame(bars_5m, columns=["time","open","high","low","close","volume"])
                df_1m   = pd.DataFrame(bars_1m, columns=["time","open","high","low","close","volume"])
                df_5m["time"] = pd.to_datetime(df_5m["time"], unit="ms")
                df_1m["time"] = pd.to_datetime(df_1m["time"], unit="ms")
            except Exception as e:
                print(f"[FETCH ERROR] {e}")
                time.sleep(10)
                continue

            if df_5m is None or df_1m is None or len(df_1m) < 20:
                time.sleep(10)
                continue

            current_price = df_1m["close"].iloc[-1]
            atr           = calc_atr(df_1m, ATR_PERIOD)

            structure_5m  = detect_structure(df_5m, swing_bars=3)
            structure_1m  = detect_structure(df_1m, swing_bars=3)
            liq           = detect_liquidity(df_1m, lookback=30)
            obs           = detect_order_blocks(df_1m, lookback=20)
            fvgs          = detect_fvg(df_1m, lookback=20)

            points, direction, reasons = smart_money_score(
                structure_5m, structure_1m, liq, obs, fvgs
            )

            confidence = int((points / 8) * 100)

            if points >= MIN_SCORE_POINTS and direction == "BUY":
                signal = "BUY"
            elif points >= MIN_SCORE_POINTS and direction == "SELL":
                signal = "SELL"
            else:
                signal = "WAIT"

            print(f"[SCALP] {scan_time} | {points}/8 | {signal} | ATR={atr:.2f} | Price={current_price:.2f}")

            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write(
                    f"SIGNAL:{signal}\n"
                    f"CONFIDENCE:{confidence}\n"
                    f"SCORE:{points}\n"
                    f"ATR:{round(atr, 4)}\n"
                    f"TIME:{scan_time}\n"
                    f"REASON:{' | '.join(reasons)}\n"
                )

            update_state(
                last_signal=signal,
                last_conf=confidence,
                last_points=points,
                last_price=current_price,
            )

            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    log = json.load(f)
            except:
                log = []
            log.append({
                "time": scan_time, "signal": signal,
                "points": points, "atr": round(atr, 4),
                "reasons": reasons,
            })
            log = log[-2000:]
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

    print("[SCALP EXECUTE] Waiting for signal...")
    while True:
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                if "SIGNAL:" in f.read():
                    break
        except:
            pass
        time.sleep(10)

    print("[SCALP EXECUTE] Started!")
    send_telegram(
        f"SCALPING BOT v2.0 STARTED\n"
        f"Capital  : {capital:.2f} USDT\n"
        f"Symbol   : {SYMBOL}\n"
        f"Mode     : Paper Trading\n"
        f"Strategy : Smart Money\n"
        f"Leverage : {LEVERAGE}x\n"
        f"Max Hold : {MAX_HOLD_MINUTES} min\n"
        f"Cooldown : {COOLDOWN} sec\n"
        f"Sessions : London + NY"
    )

    while True:
        try:
            # Read signal
            try:
                with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
                data = {}
                for line in lines:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        data[k.strip()] = v.strip()
                signal     = data.get("SIGNAL", "WAIT")
                confidence = int(data.get("CONFIDENCE", "0"))
                score      = float(data.get("SCORE", "0"))
                reason     = data.get("REASON", "")
                atr        = float(data.get("ATR", "0"))
            except:
                signal, confidence, score, reason, atr = "WAIT", 0, 0.0, "", 0.0

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

            # Max hold check
            if position is not None and entry_time is not None:
                held = (datetime.now() - entry_time).seconds / 60
                if held >= MAX_HOLD_MINUTES:
                    pnl      = calc_pnl(position, entry_price, current_price, pos_size)
                    capital += pnl
                    duration = str(datetime.now() - entry_time).split(".")[0]
                    save_capital(capital)
                    save_trade_history(position, entry_price, current_price,
                                       pnl, capital, duration, "Max Hold")
                    send_telegram(
                        f"SCALP CLOSED — Max Hold\n"
                        f"Side    : {position}\n"
                        f"Entry   : {entry_price:.2f}\n"
                        f"Exit    : {current_price:.2f}\n"
                        f"PnL     : {pnl:+.2f} USDT\n"
                        f"Capital : {capital:.2f} USDT\n"
                        f"Time    : {duration}"
                    )
                    position = None; entry_price = 0.0; entry_time = None
                    pos_size = 0.0; sl_price = 0.0; tp_price = 0.0
                    capital_used = 0.0
                    cooldown_end = time.time() + COOLDOWN
                    update_state(position=None, capital_used=0.0, capital=capital)
                    time.sleep(EXECUTE_SCAN)
                    continue

            # TP Zone check
            if position is not None:
                if position == "BUY":
                    tp_prog = (current_price - entry_price) / (tp_price - entry_price) \
                              if tp_price != entry_price else 0
                else:
                    tp_prog = (entry_price - current_price) / (entry_price - tp_price) \
                              if tp_price != entry_price else 0

                if TP_EXIT_MIN_PCT <= tp_prog <= TP_EXIT_MAX_PCT:
                    pts = get_state("last_points")
                    if pts < TP_HOLD_MIN_SCORE:
                        pnl      = calc_pnl(position, entry_price, current_price, pos_size)
                        capital += pnl
                        duration = str(datetime.now() - entry_time).split(".")[0]
                        save_capital(capital)
                        save_trade_history(position, entry_price, current_price,
                                           pnl, capital, duration, "Early Exit")
                        update_state(last_tp_zone=f"TP {tp_prog*100:.0f}% exit | Score={pts}/8 | PnL={pnl:+.2f}")
                        send_telegram(
                            f"SCALP EARLY EXIT\n"
                            f"Side  : {position}\n"
                            f"Entry : {entry_price:.2f}\n"
                            f"Exit  : {current_price:.2f}\n"
                            f"PnL   : {pnl:+.2f} USDT\n"
                            f"Zone  : {tp_prog*100:.0f}%\n"
                            f"Score : {pts}/8 weak"
                        )
                        position = None; entry_price = 0.0; entry_time = None
                        pos_size = 0.0; sl_price = 0.0; tp_price = 0.0
                        capital_used = 0.0
                        cooldown_end = time.time() + COOLDOWN
                        update_state(position=None, capital_used=0.0, capital=capital)
                        time.sleep(EXECUTE_SCAN)
                        continue
                    else:
                        update_state(last_tp_zone=f"TP {tp_prog*100:.0f}% | Score={pts}/8 strong")
                else:
                    update_state(last_tp_zone="")

            # Trailing SL
            if position is not None:
                if position == "BUY":
                    p_pct = ((current_price - entry_price) / entry_price) * 100
                    if p_pct >= 0.3:
                        new_sl = current_price * (1 - 0.2 / 100)
                        if new_sl > sl_price:
                            sl_price = new_sl
                            update_state(sl_price=sl_price)
                elif position == "SELL":
                    p_pct = ((entry_price - current_price) / entry_price) * 100
                    if p_pct >= 0.3:
                        new_sl = current_price * (1 + 0.2 / 100)
                        if new_sl < sl_price:
                            sl_price = new_sl
                            update_state(sl_price=sl_price)

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
                    save_trade_history(position, entry_price, current_price,
                                       pnl, capital, duration, label)
                    send_telegram(
                        f"SCALP CLOSED — {label}\n"
                        f"Side    : {position}\n"
                        f"Entry   : {entry_price:.2f}\n"
                        f"Exit    : {current_price:.2f}\n"
                        f"PnL     : {pnl:+.2f} USDT\n"
                        f"Capital : {capital:.2f} USDT\n"
                        f"Time    : {duration}"
                    )
                    position = None; entry_price = 0.0; entry_time = None
                    pos_size = 0.0; sl_price = 0.0; tp_price = 0.0
                    capital_used = 0.0
                    cooldown_end = time.time() + COOLDOWN
                    update_state(position=None, capital_used=0.0,
                                 capital=capital, last_tp_zone="")
                    time.sleep(EXECUTE_SCAN)
                    continue

            # Cooldown
            if cooldown_end is not None and time.time() < cooldown_end:
                print(f"[{now}] Cooldown baaki")
                time.sleep(EXECUTE_SCAN)
                continue

            # Market hours
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
                        sl_pct = 0.15
                        tp_pct = 0.30

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

                    send_telegram(
                        f"SCALP OPENED\n"
                        f"Side    : {position}\n"
                        f"Entry   : {entry_price:.2f}\n"
                        f"SL      : {sl_price:.2f}\n"
                        f"TP      : {tp_price:.2f}\n"
                        f"ATR     : {atr:.2f}\n"
                        f"Capital : {capital_used:.2f} USDT\n"
                        f"Score   : {int(score)}/8\n"
                        f"Reason  : {reason[:200]}"
                    )
                else:
                    print(f"[{now}] WAIT | Score={int(score)}/8 | Price={current_price:.2f}")
            else:
                pnl_now = calc_pnl(position, entry_price, current_price, pos_size)
                print(f"[{now}] Holding {position} | PnL={pnl_now:+.2f} | Price={current_price:.2f}")

        except Exception as e:
            print(f"[EXECUTE ERROR] {e}")
            send_telegram(f"SCALP EXECUTE ERROR!\n{str(e)[:200]}")
            time.sleep(30)

        time.sleep(EXECUTE_SCAN)


# ─────────────────────────────────────────────
#  START
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  SCALPING BOT v2.0")
    print("  Strategy : Smart Money")
    print("  Target   : Chote profits, zyada trades")
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

    print("[INFO] All started!")
    print("[INFO] Flask    : port 8081")
    print("[INFO] Decision : har 30s")
    print("[INFO] Execute  : har 2s")
    print("[INFO] Max Hold : 5 min")

    while True:
        time.sleep(60)
