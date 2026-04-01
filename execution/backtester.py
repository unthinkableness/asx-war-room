"""
Historical Weight Optimizer (Layer 3 Execution)
==============================================
Downloads 6 months of historical ASX data for the watchlist and
uses grid-search optimization to find the mathematically ideal weightings
for Relative Volume, ATR, VWAP Distance, and Gap-ups that yield the
highest 3-day forward momentum.

Writes to: data/optimal_weights.json
"""

import os
import sys
import json
import logging
import pandas as pd
import numpy as np
import yfinance as yf
from scipy.stats import zscore
import itertools
from datetime import datetime

# Setup
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from execution.asx_scanner import WATCHLIST

logging.basicConfig(level=logging.INFO, format="%(asctime)s - Optimizer - %(levelname)s - %(message)s")
logger = logging.getLogger("Optimizer")

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)
WEIGHTS_FILE = os.path.join(DATA_DIR, "optimal_weights.json")


def fetch_and_prepare_data(tickers, period="6mo"):
    """Downloads and vectorizes metrics for the entire dataframe."""
    logger.info(f"Downloading {period} historical data for {len(tickers)} tickers. This may take a minute...")
    all_data = []

    for ticker in tickers:
        try:
            df = yf.Ticker(ticker).history(period=period)
            if len(df) < 30:
                continue

            # Target: 3-day forward return
            df['fwd_3d_ret'] = (df['Close'].shift(-3) - df['Close']) / df['Close']

            # Calculate RVol (10 day)
            df['avg_vol'] = df['Volume'].rolling(window=10).mean()
            df['rvol'] = np.where(df['avg_vol'] > 0, df['Volume'] / df['avg_vol'], 0)

            # Calculate ATR% (14 day)
            high_low = df['High'] - df['Low']
            high_cp = (df['High'] - df['Close'].shift()).abs()
            low_cp = (df['Low'] - df['Close'].shift()).abs()
            tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
            atr = tr.rolling(window=14).mean()
            df['atr_pct'] = (atr / df['Close']) * 100

            # Calculate VWAP Distance
            # Simplified daily VWAP approximation for daily bars: Typical Price
            typical_price = (df['High'] + df['Low'] + df['Close']) / 3
            # We approximate vwap over a 5 day window since we only have daily bars
            vwap = (typical_price * df['Volume']).rolling(5).sum() / df['Volume'].rolling(5).sum()
            df['vwap_dist_pct'] = ((df['Close'] - vwap) / vwap) * 100

            # Gap-Up %
            prev_close = df['Close'].shift(1)
            df['gap_pct'] = ((df['Open'] - prev_close) / prev_close) * 100
            # Normalize to multipliers if gap is over 2% and elevated volume
            df['is_gap'] = (df['gap_pct'] >= 2.0) & (df['Volume'] > df['avg_vol'] * 1.5)
            
            # Drop NaNs
            df = df.dropna(subset=['fwd_3d_ret', 'rvol', 'atr_pct', 'vwap_dist_pct'])

            # Normalize continuous features so grid search makes sense (Z-score fallback to simple minmax)
            # Clip extreme outliers
            df['rvol_norm'] = df['rvol'].clip(0, 10) / 10.0
            df['atr_norm'] = df['atr_pct'].clip(0, 15) / 15.0
            df['vwap_norm'] = (df['vwap_dist_pct'].clip(-10, 10) + 10) / 20.0
            df['gap_bonus'] = np.where(df['is_gap'], 1.5, 1.0)

            all_data.append(df[['rvol_norm', 'atr_norm', 'vwap_norm', 'gap_bonus', 'fwd_3d_ret']])
            
        except Exception as e:
            logger.warning(f"Failed to process {ticker}: {e}")

    if not all_data:
        return pd.DataFrame()
        
    return pd.concat(all_data, ignore_index=True)


def grid_search_optimization(df_master):
    """
    Tests multiple combinations of weights.
    Score = W1*RVol + W2*ATR + W3*VWAP + W4*GapBonus
    Returns the weights that maxed out the top 5% PnL.
    """
    logger.info(f"Assembled {len(df_master)} historical trading days for analysis.")
    logger.info("Running deterministic grid search...")

    best_win_rate = -np.inf
    best_weights = {'w_rvol': 1.0, 'w_atr': 1.0, 'w_vwap': 1.0, 'w_gap': 1.0}

    # Weight steps from 0.5 to 2.5
    weight_range = [0.5, 1.0, 1.5, 2.0, 2.5]
    
    combinations = list(itertools.product(weight_range, repeat=4))
    
    for w1, w2, w3, w4 in combinations:
        # Vectorized Score Calculation
        df_master['sim_score'] = (
            (df_master['rvol_norm'] * w1) + 
            (df_master['atr_norm'] * w2) + 
            (df_master['vwap_norm'] * w3)
        ) * (df_master['gap_bonus'] * w4)

        # Take the top 5% highest scoring days ("Signals")
        threshold = df_master['sim_score'].quantile(0.95)
        top_signals = df_master[df_master['sim_score'] >= threshold]

        if len(top_signals) == 0:
            continue

        # Criteria: Average 3-day forward return of the top signals
        avg_3d_ret = top_signals['fwd_3d_ret'].mean()

        if avg_3d_ret > best_win_rate:
            best_win_rate = avg_3d_ret
            best_weights = {
                'w_rvol': w1,
                'w_atr': w2,
                'w_vwap': w3,
                'w_gap': w4
            }

    logger.info(f"Optimal weights found for {best_win_rate*100:.2f}% avg 3-day return:")
    for k, v in best_weights.items():
        logger.info(f"  {k} = {v}")

    # Scale them relative to 1.0 for ease of reading
    base = sum(best_weights.values()) / 4
    rel_weights = {k: round(v/base, 2) for k, v in best_weights.items()}
    
    # Save the file
    out = {
        "updated_at": datetime.now().isoformat(),
        "optimized_return_pct": round(best_win_rate * 100, 2),
        "weights": rel_weights
    }
    
    with open(WEIGHTS_FILE, "w") as f:
        json.dump(out, f, indent=2)

    logger.info(f"Saved to {WEIGHTS_FILE}")
    return rel_weights


if __name__ == "__main__":
    df_all = fetch_and_prepare_data(WATCHLIST, period="6mo")
    if not df_all.empty:
        grid_search_optimization(df_all)
    else:
        logger.error("Failed to assemble historical dataset.")
