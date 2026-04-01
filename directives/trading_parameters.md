# ASX Trading Parameters (High-Risk) — v2

This directive defines the operational parameters for the "Momentum Hunter" strategy.

## 1. Stock Selection Criteria (The Scanner)

Scanners must be run between 8:00 AM and 9:55 AM AEST. Only stocks meeting ALL criteria below are added to the day's "High Probability" list.

- **Market Cap:** $50M - $300M (The "Sweet Spot" for volatility).
- **Sector Focus:** Resources (Critical Minerals, Gold), Biotech, AI/Tech.
- **Relative Volume (RVol):** > 2.5x the 10-day average.
- **Price Trend:** Stock must be trading above its 20-day Simple Moving Average (SMA).
- **Sentiment Score:** > 0.7 (via NLP or keyword `hotcopper_scraper.py`).

### v2 Additions

- **VWAP Positioning:** Stocks trading above VWAP receive a **1.2x score multiplier** (strong buyer control). Below VWAP = **0.8x penalty** (volume trap / distribution risk).
- **Gap-and-Go Detection:** If today's open is ≥ 2% above yesterday's close on elevated volume (≥ 1.5x avg), the stock receives a **1.5x score bonus**. This is the strongest short-term momentum signal.
- **Composite Score Formula:** `score = RVol × ATR% × VWAP_multiplier × Gap_bonus`

## 2. Sentiment Analysis (v2)

- **Primary Method (NLP):** FinBERT (`ProsusAI/finbert`) classifies post titles as positive/negative/neutral with confidence scores. Requires `transformers` library.
- **Fallback Method (Keywords):** Basic bullish/bearish keyword counting if FinBERT is unavailable.
- **Social Velocity:** `posts_current / baseline_posts`. Velocity > 3.0 = "trending" (+0.05 bonus), > 5.0 = "viral" (+0.10 bonus). Catches stocks before they peak.

## 3. Risk Management & Position Sizing

- **Total Capital:** $50,000 (Game Start).
- **Number of Holdings:** Maximum 4 stocks.
- **Allocation:** Exactly 25% of the total portfolio value per stock at purchase.
- **The "Falling Sell" (Game Tool):** A trailing stop loss of 8-10% must be set immediately upon order confirmation.

## 4. Order Execution Rules (Live-on-Live)

- **The "Nitro Pulse" Velocity Rule:** Orders are submitted as **At-Market** the moment the scanner detects an intraday volume spike (5x avg) or a HotCopper sentiment surge (>3 posts in 5m). We enter before the technical breakout is visible on daily charts.
- **Immediate Fills:** Since the game is live, we do not wait for a confirmation signal. Speed of entry is the primary competitive edge.
- **Holding Period:** Minimum 1 full trading day as per game rules.
