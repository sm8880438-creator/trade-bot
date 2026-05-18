"""
TRADE BOT v4.1 — Main Entry Point
Fix: Periodic update thread sync issue fixed
"""

import threading
import time
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Trade Bot Running! 🤖"

def run_server():
    app.run(host='0.0.0.0', port=8080)


import ccxt
import pandas as pd
import numpy as np
import json
import requests
from datetime import datetime

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
SYMBOL         = "ETH/USDT:USDT"
API_KEY        = ""
API_SECRET     = ""

TIMEFRAMES = {
    "1w":  0.30,
    "1d":  0.25,
    "4h":  0.25,
    "1h":  0.20,
}

DECISION_SCAN   = 300
OUTPUT_FILE     = "decision_output.txt"
LOG_FILE        = "decision_log.json"
BUY_THRESHOLD   =  0.25
SELL_THRESHOLD  = -0.25
FVG_LOOKBACK    = 50
MIN_FVG_SIZE    = 0.05

BOT_TOKEN       = "8161773850:AAFcWw3UnlSe2TrMooB2uvgZQZUqIW0zW2w"
CHAT_ID         = "7102976298"
CAPITAL         = 10000
RISK_PERCENT    = 5
LEVERAGE        = 5
STOP_LOSS_PCT   = 0.8
TAKE_PROFIT_PCT = 0.8
MIN_CONFIDENCE  = 50

TRAILING_STOP   = True
TRAIL_TRIGGER   = 0.4
TRAIL_OFFSET    = 0.3

UPDATE_INTERVAL  = 1800
MIN_SCORE_POINTS = 6


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
def detect_fvg(df, lookback=50, min_gap_pct=0.05):
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
                    "fresh":  (i >= n - 5),
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
                    "fresh":  (i >= n - 5),
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
        reasons.append("Weekly RANGE — weak (0)")
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

    reasons.append(f"Total score: {points}/8")
    return points, direction, reasons


# ─────────────────────────────────────────────
#  TIMEFRAME ANALYSIS
# ─────────────────────────────────────────────
def analyze_timeframe(exchange, symbol, tf):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=200)
    except Exception as e:
        return {"score": 0.0, "reasons": [f"{tf}: fetch error"], "error": True}
    df = pd.DataFrame(bars, columns=["time", "open", "high", "low", "close", "volume"])
    if df.empty or len(df) < 60:
        return {"score": 0.0, "reasons": [f"{tf}: data kam"], "error": True}
    df["time"]    = pd.to_datetime(df["time"], unit="ms")
    structure     = detect_structure(df)
    fvgs          = detect_fvg(df, FVG_LOOKBACK, MIN_FVG_SIZE)
    current_price = df["close"].iloc[-1]
    score, reason = fvg_signal(fvgs, structure, current_price)
    bull_c        = len([f for f in fvgs if f["type"] == "BULL"])
    bear_c        = len([f for f in fvgs if f["type"] == "BEAR"])
    near_level, _ = detect_key_levels(df, current_price)
    return {
        "score":      score,
        "fvg_score":  score,
        "reasons":    [f"{tf}: {structure} | {reason}"],
        "structure":  structure,
        "fvg_bull":   bull_c,
        "fvg_bear":   bear_c,
        "near_level": near_level,
        "price":      current_price,
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
    return (exit_price - entry) * pos_size if side == "BUY" else (entry - exit_price) * pos_size


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
}


# ─────────────────────────────────────────────
#  PERIODIC UPDATE — FIXED
# ─────────────────────────────────────────────
def run_periodic_update():
    time.sleep(UPDATE_INTERVAL)
    while True:
        try:
            now          = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            position     = trade_state["position"]
            price        = trade_state["last_price"]
            signal       = trade_state["last_signal"]
            conf         = trade_state["last_conf"]
            capital      = trade_state["capital"]
            points       = trade_state["last_points"]

            # Price 0 hai to skip
            if price == 0:
                time.sleep(UPDATE_INTERVAL)
                continue

            # Trade open hai
            if position is not None:
                entry        = trade_state["entry_price"]
                sl           = trade_state["sl_price"]
                tp           = trade_state["tp_price"]
                psize        = trade_state["pos_size"]
                etime        = trade_state["entry_time"]
                capital_used = trade_state["capital_used"]
                pnl          = calc_pnl(position, entry, price, psize)
                dur          = str(datetime.now() - etime).split(".")[0]
                pnl_icon     = "+" if pnl >= 0 else ""

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
                    f"Signal       : {signal} ({conf}%)\n"
                    f"Score        : {points}/8"
                )

            # Koi trade nahi
            else:
                send_telegram(
                    f"--- MARKET UPDATE ---\n"
                    f"Time    : {now}\n"
                    f"Price   : {price:.2f}\n"
                    f"Signal  : {signal} ({conf}%)\n"
                    f"Score   : {points}/8\n"
                    f"Capital : {capital:.2f} USDT\n"
                    f"Status  : Next entry ka wait...\n"
                    f"---------------------"
                )

        except Exception as e:
            print(f"[UPDATE ERROR] {e}")
        time.sleep(UPDATE_INTERVAL)


# ─────────────────────────────────────────────
#  DECISION ENGINE
# ─────────────────────────────────────────────
def run_decision_engine():
    exchange = get_exchange()
    print("[DECISION] Engine v4.1 started")
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

            print(f"[DECISION] {scan_time} | Points={points}/8 | Conf={confidence}% | {signal}")

            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write(
                    f"SIGNAL:{signal}\n"
                    f"CONFIDENCE:{confidence}\n"
                    f"SCORE:{points}\n"
                    f"TIME:{scan_time}\n"
                    f"REASON:{' | '.join(all_reasons)}\n"
                )

            trade_state["last_signal"] = signal
            trade_state["last_conf"]   = confidence
            trade_state["last_points"] = points

            entry_log = {
                "time":       scan_time,
                "signal":     signal,
                "confidence": confidence,
                "points":     points,
                "direction":  direction,
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
        time.sleep(DECISION_SCAN)


# ─────────────────────────────────────────────
#  EXECUTION ENGINE
# ─────────────────────────────────────────────
def run_execution_engine():
    ex           = ccxt.binanceusdm({"enableRateLimit": True})
    capital      = CAPITAL
    position     = None
    entry_price  = 0.0
    entry_time   = None
    pos_size     = 0.0
    sl_price     = 0.0
    tp_price     = 0.0
    capital_used = 0.0

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
        f"TRADE BOT v4.1 STARTED\n"
        f"Capital  : {capital} USDT\n"
        f"Symbol   : {SYMBOL}\n"
        f"Mode     : Paper Trading\n"
        f"Edge     : Weekly+Daily+4H+FVG\n"
        f"Min Score: {MIN_SCORE_POINTS}/8"
    )

    while True:
        try:
            signal, confidence, score, reason = read_signal()
            ticker        = ex.fetch_ticker(SYMBOL)
            current_price = float(ticker["last"])
            now           = datetime.now().strftime("%H:%M:%S")

            # Shared state update
            trade_state["last_price"]   = current_price
            trade_state["capital"]      = capital
            trade_state["position"]     = position
            trade_state["entry_price"]  = entry_price
            trade_state["entry_time"]   = entry_time
            trade_state["sl_price"]     = sl_price
            trade_state["tp_price"]     = tp_price
            trade_state["pos_size"]     = pos_size
            trade_state["capital_used"] = capital_used

            # Trailing SL
            if position is not None and TRAILING_STOP:
                if position == "BUY":
                    profit_pct = ((current_price - entry_price) / entry_price) * 100
                    if profit_pct >= TRAIL_TRIGGER:
                        new_sl = current_price * (1 - TRAIL_OFFSET / 100)
                        if new_sl > sl_price:
                            sl_price = new_sl
                            trade_state["sl_price"] = sl_price
                            print(f"[TRAIL] BUY SL -> {sl_price:.2f}")
                elif position == "SELL":
                    profit_pct = ((entry_price - current_price) / entry_price) * 100
                    if profit_pct >= TRAIL_TRIGGER:
                        new_sl = current_price * (1 + TRAIL_OFFSET / 100)
                        if new_sl < sl_price:
                            sl_price = new_sl
                            trade_state["sl_price"] = sl_price
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
                    trade_state["position"]     = None
                    trade_state["capital_used"] = 0.0
                    time.sleep(EXECUTE_SCAN)
                    continue

            # Entry check
            if position is None:
                if signal in ["BUY", "SELL"] and confidence >= MIN_CONFIDENCE:
                    risk_amount  = capital * (RISK_PERCENT / 100)
                    capital_used = risk_amount * LEVERAGE
                    pos_size     = capital_used / current_price
                    entry_price  = current_price
                    entry_time   = datetime.now()
                    position     = signal
                    if signal == "BUY":
                        sl_price = entry_price * (1 - STOP_LOSS_PCT / 100)
                        tp_price = entry_price * (1 + TAKE_PROFIT_PCT / 100)
                    else:
                        sl_price = entry_price * (1 + STOP_LOSS_PCT / 100)
                        tp_price = entry_price * (1 - TAKE_PROFIT_PCT / 100)
                    print(f"[EXECUTE] OPENED | {position} | Entry={entry_price:.2f} | SL={sl_price:.2f} | TP={tp_price:.2f}")
                    send_telegram(
                        f"TRADE OPENED\n"
                        f"Side         : {position}\n"
                        f"Entry        : {entry_price:.2f}\n"
                        f"SL           : {sl_price:.2f}\n"
                        f"TP           : {tp_price:.2f}\n"
                        f"Size         : {pos_size:.4f} ETH\n"
                        f"Capital Used : {capital_used:.2f} USDT\n"
                        f"Total Capital: {capital:.2f} USDT\n"
                        f"Conf         : {confidence}%\n"
                        f"Score        : {int(score)}/8\n"
                        f"Reason       : {reason[:300]}"
                    )
                else:
                    if signal == "WAIT":
                        print(f"[{now}] Price={current_price:.2f} | WAIT | Score={int(score)}/8")
                    else:
                        print(f"[{now}] Price={current_price:.2f} | Conf {confidence}% < {MIN_CONFIDENCE}% — skip")

            # Hold / Flip
            else:
                if (position == "BUY"  and signal == "SELL" and confidence >= MIN_CONFIDENCE) or \
                   (position == "SELL" and signal == "BUY"  and confidence >= MIN_CONFIDENCE):
                    pnl      = calc_pnl(position, entry_price, current_price, pos_size)
                    capital += pnl
                    duration = str(datetime.now() - entry_time).split(".")[0]
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
                    risk_amount  = capital * (RISK_PERCENT / 100)
                    capital_used = risk_amount * LEVERAGE
                    pos_size     = capital_used / current_price
                    entry_price  = current_price
                    entry_time   = datetime.now()
                    position     = signal
                    if signal == "BUY":
                        sl_price = entry_price * (1 - STOP_LOSS_PCT / 100)
                        tp_price = entry_price * (1 + TAKE_PROFIT_PCT / 100)
                    else:
                        sl_price = entry_price * (1 + STOP_LOSS_PCT / 100)
                        tp_price = entry_price * (1 - TAKE_PROFIT_PCT / 100)
                    print(f"[EXECUTE] REVERSED -> {position} | Entry={entry_price:.2f}")
                    send_telegram(
                        f"TRADE REVERSED\n"
                        f"New Side     : {position}\n"
                        f"Entry        : {entry_price:.2f}\n"
                        f"SL           : {sl_price:.2f}\n"
                        f"TP           : {tp_price:.2f}\n"
                        f"Capital Used : {capital_used:.2f} USDT\n"
                        f"Conf         : {confidence}%"
                    )
                else:
                    pnl_now = calc_pnl(position, entry_price, current_price, pos_size)
                    print(f"[{now}] Price={current_price:.2f} | {signal} | Conf={confidence}% | Holding {position} | PnL={pnl_now:+.2f} USDT")

        except Exception as e:
            print(f"[EXECUTE ERROR] {e}")

        time.sleep(EXECUTE_SCAN)


# ─────────────────────────────────────────────
#  START ALL THREADS
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  TRADE BOT v4.1 STARTING...")
    print("  Edge: Weekly+Daily+4H+FVG+KeyLevels")
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

    print("[INFO] All engines started!")
    print("[INFO] Flask    : port 8080")
    print("[INFO] Decision : har 300s")
    print("[INFO] Execute  : har 10s")
    print("[INFO] Updates  : har 30 min")

    while True:
        time.sleep(60)
