"""
ASX Momentum Scanner (Layer 3 Execution) — v2
===============================================
Scans ASX-listed stocks for high-momentum, high-volatility candidates
using yfinance for real-time (or near-real-time) data.

Returns a ranked list of tickers that meet the directive thresholds:
  - Relative Volume > 2.5x
  - Daily ATR % > 4%
  - Price above 20-day SMA
  - Market Cap $50M - $300M

v2 Additions:
  - VWAP (Volume Weighted Average Price) positioning
  - Gap-and-Go detection (open > 2% above prev close on volume)
  - Updated composite scoring: rvol * atr% * vwap_mult * gap_bonus
"""

import yfinance as yf
import numpy as np
import pandas as pd
import json
import os
import sys
from datetime import datetime, timedelta

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.logger import setup_logger

logger = setup_logger("ASXScanner")

# ---------------------------------------------------------------------------
# ASX Watchlist: High-volatility small/mid-cap tickers to scan.
# These are ASX tickers appended with ".AX" for yfinance compatibility.
# Focus: Resources (Critical Minerals, Gold, Lithium), Biotech, AI/Tech.
# ---------------------------------------------------------------------------
WATCHLIST = [
    # --- Critical Minerals & Lithium ---
    "PLS.AX",   # Pilbara Minerals
    "LTR.AX",   # Liontown Resources
    "SYA.AX",   # Sayona Mining
    "CXO.AX",   # Core Lithium
    "GL1.AX",   # Global Lithium
    "LKE.AX",   # Lake Resources
    "INR.AX",   # ioneer
    "AGY.AX",   # Argosy Minerals
    "AVZ.AX",   # AVZ Minerals
    "FFX.AX",   # Firefinch

    # --- Gold ---
    "NST.AX",   # Northern Star
    "EVN.AX",   # Evolution Mining
    "RMS.AX",   # Ramelius Resources
    "GOR.AX",   # Gold Road Resources
    "DEG.AX",   # De Grey Mining
    "WGX.AX",   # Westgold Resources
    "SBM.AX",   # St Barbara
    "RSG.AX",   # Resolute Mining
    "RED.AX",   # Red 5

    # --- Biotech ---
    "IMU.AX",   # Imugene
    "NXL.AX",   # Nuix
    "RAC.AX",   # Race Oncology
    "PNV.AX",   # PolyNovo
    "4DX.AX",   # 4DMedical
    "EMV.AX",   # EMVision

    # --- Tech / AI ---
    "BRN.AX",   # BrainChip
    "XRO.AX",   # Xero
    "WTC.AX",   # WiseTech Global
    "TNE.AX",   # TechnologyOne
    "DUB.AX",   # Dubber
    "LNK.AX",   # Link Administration

    # --- Energy / Uranium ---
    "PDN.AX",   # Paladin Energy
    "LOT.AX",   # Lotus Resources
    "BMN.AX",   # Bannerman Energy
    "DYL.AX",   # Deep Yellow
    "PEN.AX",   # Peninsula Energy
    "BOE.AX",   # Boss Energy

    # --- Speculative Small-Caps ---
    "ZIP.AX",   # Zip Co
    "NVX.AX",   # Novonix
    "VUL.AX",   # Vulcan Energy
    "LYC.AX",   # Lynas Rare Earths
    "ILU.AX",   # Iluka Resources
    "MIN.AX",   # Mineral Resources
    "29M.AX",   # 29Metals
    "SFR.AX",   # Sandfire Resources
]


def get_stock_data(ticker, period="1mo"):
    """
    Fetches historical daily data for a single ASX ticker.
    Returns a pandas DataFrame or None on failure.
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)
        if hist.empty:
            return None
        return hist
    except Exception as e:
        logger.warning(f"Failed to fetch data for {ticker}: {e}")
        return None


def get_stock_info(ticker):
    """
    Fetches the info dict for a ticker (market cap, sector, etc).
    Returns a dict or empty dict on failure.
    """
    try:
        stock = yf.Ticker(ticker)
        return stock.info
    except Exception as e:
        logger.warning(f"Failed to fetch info for {ticker}: {e}")
        return {}


def calculate_atr_percent(df, period=14):
    """
    Calculates the Average True Range as a percentage of the closing price.
    ATR% = (ATR / Close) * 100
    """
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(window=period).mean()
    atr_pct = (atr / close) * 100
    return atr_pct.iloc[-1] if not atr_pct.empty else 0.0


def calculate_relative_volume(df, lookback=10):
    """
    Calculates relative volume: today's volume vs the average of the
    last `lookback` days.
    """
    if len(df) < lookback + 1:
        return 0.0

    avg_volume = df["Volume"].iloc[-(lookback + 1):-1].mean()
    today_volume = df["Volume"].iloc[-1]

    if avg_volume == 0:
        return 0.0

    return today_volume / avg_volume


def is_above_sma(df, period=20):
    """
    Checks if the latest close is above the simple moving average.
    """
    if len(df) < period:
        return False
    sma = df["Close"].rolling(window=period).mean()
    return df["Close"].iloc[-1] > sma.iloc[-1]


def calculate_vwap(df):
    """
    Calculates the Volume Weighted Average Price for the most recent session.
    VWAP = cumulative(Price * Volume) / cumulative(Volume)

    Returns:
        vwap value (float), or None if insufficient data.
    """
    if df is None or df.empty:
        return None

    try:
        # Typical price = (High + Low + Close) / 3
        typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
        cum_vol = df["Volume"].cumsum()
        cum_vol_price = (typical_price * df["Volume"]).cumsum()

        vwap = cum_vol_price / cum_vol
        return round(float(vwap.iloc[-1]), 4) if not vwap.empty else None
    except Exception:
        return None


def calculate_vwap_distance(df):
    """
    Returns the percentage distance of the current price from VWAP.
    Positive = price is above VWAP (bullish).
    Negative = price is below VWAP (bearish trap).
    """
    vwap = calculate_vwap(df)
    if vwap is None or vwap == 0:
        return 0.0, None

    current_price = float(df["Close"].iloc[-1])
    distance_pct = ((current_price - vwap) / vwap) * 100
    return round(distance_pct, 2), round(vwap, 4)


def get_vwap_multiplier(df):
    """
    Returns a score multiplier based on VWAP positioning.
    Above VWAP = 1.2x bonus (strong buyers in control).
    Below VWAP = 0.8x penalty (volume trap / distribution).
    """
    distance_pct, _ = calculate_vwap_distance(df)
    if distance_pct > 0:
        return 1.2
    else:
        return 0.8


def detect_gap_up(df, threshold_pct=2.0):
    """
    Detects a "Gap-and-Go" pattern: today's open is >= threshold_pct%
    above yesterday's close, on elevated volume.

    This is the single strongest short-term momentum signal on the ASX.

    Returns:
        (is_gap_up: bool, gap_pct: float)
    """
    if df is None or len(df) < 2:
        return False, 0.0

    try:
        prev_close = float(df["Close"].iloc[-2])
        today_open = float(df["Open"].iloc[-1])

        if prev_close <= 0:
            return False, 0.0

        gap_pct = ((today_open - prev_close) / prev_close) * 100

        # Also check volume is elevated (at least 1.5x average)
        avg_vol = float(df["Volume"].iloc[:-1].mean())
        today_vol = float(df["Volume"].iloc[-1])
        vol_elevated = today_vol > (avg_vol * 1.5) if avg_vol > 0 else False

        is_gap = gap_pct >= threshold_pct and vol_elevated
        return is_gap, round(gap_pct, 2)
    except Exception:
        return False, 0.0


def get_live_price(ticker):
    """
    Gets the most recent price for a ticker.
    This is the "live" price that we compare to the game's 20-min delayed price.
    """
    try:
        stock = yf.Ticker(ticker)
        # fast_info provides the most recent data
        return stock.fast_info.get("last_price", None)
    except Exception:
        return None

def get_sector_proxy_performance():
    """
    Fetches the daily performance of the broader market (ASX 200: ^AXJO).
    Returns a multiplier: 1.0 normally, 0.5 if market is crashing (>1% down).
    """
    try:
        df = yf.Ticker("^AXJO").history(period="2d")
        if len(df) >= 2:
            prev_close = df["Close"].iloc[-2]
            current = df["Close"].iloc[-1]
            pct_change = ((current - prev_close) / prev_close) * 100
            logger.info(f"ASX200 Performance: {pct_change:.2f}%")
            if pct_change <= -1.0:
                logger.warning("🚨 SECTOR DUMP DETECTED. Applying 50% penalty to all scores.")
                return 0.5
    except Exception as e:
        logger.warning(f"Failed to get sector proxy: {e}")
    return 1.0

def load_optimal_weights():
    """Loads weights generated by the backtester. Default fallback if missing."""
    weights_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "optimal_weights.json")
    default_weights = {'w_rvol': 1.0, 'w_atr': 1.0, 'w_vwap': 1.0, 'w_gap': 1.0}
    try:
        with open(weights_path, "r") as f:
            data = json.load(f)
            return data.get("weights", default_weights)
    except Exception:
        logger.warning("No optimal_weights.json found. Using default 1.0 weights.")
        return default_weights

def scan_momentum(
    watchlist=None,
    min_rvol=2.5,
    min_atr_pct=4.0,
    min_mcap=50_000_000,
    max_mcap=300_000_000,
    require_above_sma=True
):
    """
    Main scanning function.
    Iterates through the watchlist and scores each stock.

    Returns a list of dicts sorted by a composite score:
    [
        {
            "ticker": "BRN.AX",
            "asx_code": "BRN",
            "price": 0.45,
            "rvol": 3.2,
            "atr_pct": 6.1,
            "above_sma": True,
            "market_cap": 120000000,
            "score": 19.52
        },
        ...
    ]
    """
    if watchlist is None:
        watchlist = WATCHLIST

    signals = []
    total = len(watchlist)

    logger.info(f"Scanning {total} tickers...")
    
    # Load V3 Predictive Constraints
    weights = load_optimal_weights()
    W_RVOL = weights['w_rvol']
    W_ATR = weights['w_atr']
    W_VWAP = weights['w_vwap']
    W_GAP = weights['w_gap']
    
    sector_mult = get_sector_proxy_performance()

    for i, ticker in enumerate(watchlist):
        asx_code = ticker.replace(".AX", "")
        logger.info(f"[{i+1}/{total}] Scanning {asx_code}...")

        # Fetch data
        df = get_stock_data(ticker)
        if df is None or len(df) < 20:
            logger.info(f"  -> Skipped (insufficient data)")
            continue

        # Calculate metrics
        rvol = calculate_relative_volume(df)
        atr_pct = calculate_atr_percent(df)
        above_sma = is_above_sma(df)
        price = df["Close"].iloc[-1]

        # v2: VWAP and Gap-Up
        vwap_dist, vwap_val = calculate_vwap_distance(df)
        vwap_mult = get_vwap_multiplier(df)
        is_gap, gap_pct = detect_gap_up(df)

        # Market cap check (optional, may fail for some tickers)
        info = get_stock_info(ticker)
        market_cap = info.get("marketCap", 0)

        # Apply filters
        if rvol < min_rvol:
            logger.info(f"  -> Skipped (RVol={rvol:.2f} < {min_rvol})")
            continue
        if atr_pct < min_atr_pct:
            logger.info(f"  -> Skipped (ATR%={atr_pct:.2f} < {min_atr_pct})")
            continue
        if require_above_sma and not above_sma:
            logger.info(f"  -> Skipped (below 20-day SMA)")
            continue
        if market_cap and (market_cap < min_mcap or market_cap > max_mcap):
            logger.info(f"  -> Skipped (MCap=${market_cap:,.0f} out of range)")
            continue

        # V3 Machine-Learned Composite score
        rvol_norm = min(rvol, 10.0) / 10.0
        atr_norm = min(atr_pct, 15.0) / 15.0
        vwap_norm = (max(min(vwap_dist, 10.0), -10.0) + 10.0) / 20.0
        gap_bonus = 1.5 if is_gap else 1.0
        
        raw_score = ((rvol_norm * W_RVOL) + (atr_norm * W_ATR) + (vwap_norm * W_VWAP)) * (gap_bonus * W_GAP)
        score = raw_score * 100 * sector_mult

        signal = {
            "ticker": ticker,
            "asx_code": asx_code,
            "price": round(price, 4),
            "rvol": round(rvol, 2),
            "atr_pct": round(atr_pct, 2),
            "above_sma": above_sma,
            "market_cap": market_cap,
            "vwap": vwap_val,
            "vwap_distance_pct": vwap_dist,
            "vwap_position": "above" if vwap_dist > 0 else "below",
            "gap_up": is_gap,
            "gap_pct": gap_pct,
            "vwap_multiplier": vwap_mult,
            "gap_bonus": gap_bonus,
            "score": round(score, 2),
            "scanned_at": datetime.now().isoformat()
        }
        signals.append(signal)
        gap_tag = " GAP-UP!" if is_gap else ""
        vwap_tag = "↑" if vwap_dist > 0 else "↓"
        logger.info(f"  ✓ SIGNAL: {asx_code} | ${price:.4f} | RVol={rvol:.2f} | ATR%={atr_pct:.2f} | VWAP{vwap_tag}{vwap_dist:+.1f}% | Score={score:.2f}{gap_tag}")

    # Sort by composite score descending
    signals.sort(key=lambda x: x["score"], reverse=True)

    logger.info(f"Scan complete. {len(signals)} signals found out of {total} tickers.")
    return signals


def get_asx_momentum_list(top_n=10):
    """
    Public API: Returns the top N momentum candidates.
    Called by the orchestrator.
    """
    all_signals = scan_momentum()
    top = all_signals[:top_n]

    # Save to data dir for persistence ONLY if we have new signals
    if len(top) > 0:
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        os.makedirs(data_dir, exist_ok=True)
        output_path = os.path.join(data_dir, "latest_scan.json")

        with open(output_path, "w") as f:
            json.dump(top, f, indent=2)

        logger.info(f"Top {len(top)} signals saved to {output_path}")
    else:
        logger.info("No momentum signals found. Retaining previous scan payload in memory.")

    return top


if __name__ == "__main__":
    print("=" * 60)
    print("ASX MOMENTUM SCANNER v2")
    print("=" * 60)

    results = get_asx_momentum_list(top_n=10)

    if results:
        print(f"\n{'Rank':<5} {'Code':<6} {'Price':>10} {'RVol':>6} {'ATR%':>6} {'VWAP%':>7} {'Gap%':>6} {'Score':>8}")
        print("-" * 62)
        for i, r in enumerate(results):
            gap_flag = "⚡" if r.get('gap_up') else " "
            print(f"{i+1:<5} {r['asx_code']:<6} ${r['price']:>8.4f} {r['rvol']:>5.2f}x "
                  f"{r['atr_pct']:>5.2f}% {r.get('vwap_distance_pct', 0):>+6.1f}% "
                  f"{r.get('gap_pct', 0):>5.1f}% {r['score']:>7.2f} {gap_flag}")
    else:
        print("No signals found matching criteria.")
