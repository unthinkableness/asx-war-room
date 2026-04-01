# ASX Watchlist & Rotation Rules

This directive governs how the automated system manages the "Active Watchlist" and "Portfolio Rotation."

## 1. Watchlist Maintenance

- **Scanning Frequency:** Daily between 8:00 AM - 10:00 AM AEST.
- **Top 10 Filter:** The system must only track the top 10 stocks that meet the `trading_parameters.md` filters.
- **Sentiment Refresh:** Sentiment scores on the Top 10 list must be refreshed every 4 hours during market hours.

## 2. Rotation & Selling Rules (The Exit)

Given the high-risk nature, capital preservation is key once a momentum "pump" stalls.

- **Stagnation Rule:** If a stock has NOT moved > 3% in 3 trading days, exit the position immediately (Sell at Market).
- **Sentiment Drop:** If the sentiment score drops below 0.4 (HotCopper thread volume sinks), it's highly likely the "hype" has transitioned, and we should sell.
- **Technical Breakdown:** If the price falls below the 20-day SMA, sell.
- **Capital Reallocation:** Upon a `SELL` event, the system should immediately identify the next top-ranked stock on the "High Probability" watchlist and buy 25% allocation.

## 3. The "Moon Shot" Rule

- **Gains Locking:** If a stock rises > 20% in a single day, the "Falling Sell" (Trailing Stop) should be tightened to 5% instead of the standard 10% to "lock in" massive gains.
