"""
Portfolio Manager (Layer 3 Execution)
======================================
Manages portfolio state, position sizing, and trade decisions.

Responsibilities:
  - Track current holdings and cash balance
  - Calculate 25% position sizes per the directive
  - Enforce the game's diversification rule (max 25% per stock at purchase)
  - Determine which stocks to rotate in/out
  - Track the 1-day holding period
"""

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.logger import setup_logger

logger = setup_logger("PortfolioManager")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
PORTFOLIO_PATH = os.path.join(DATA_DIR, "portfolio.json")
TRADE_LOG_PATH = os.path.join(DATA_DIR, "trade_log.csv")

# Game constants
BROKERAGE_FLAT = 15.0           # $15 for orders up to $15,000
BROKERAGE_PCT = 0.001           # 0.1% for orders > $15,000
MAX_HOLDINGS = 4                # Game minimum is 4; we use exactly 4
MAX_ALLOCATION_PCT = 0.25       # 25% diversification rule
INITIAL_CAPITAL = 50000.0
MIN_HOLDING_DAYS = 1            # Must hold for at least 1 full trading day


def load_portfolio():
    """
    Loads the current portfolio state from disk.
    Returns a dict with cash, holdings, etc.
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    if os.path.exists(PORTFOLIO_PATH):
        with open(PORTFOLIO_PATH, "r") as f:
            portfolio = json.load(f)
        logger.info(f"Portfolio loaded: ${portfolio.get('cash_balance', 0):,.2f} cash, "
                    f"{len(portfolio.get('holdings', []))} holdings")
        return portfolio

    # Default initial state
    default = {
        "last_updated": None,
        "cash_balance": INITIAL_CAPITAL,
        "holdings": [],
        "watchlist": []
    }
    save_portfolio(default)
    return default


def save_portfolio(portfolio):
    """Saves the portfolio state to disk."""
    os.makedirs(DATA_DIR, exist_ok=True)
    portfolio["last_updated"] = datetime.now().isoformat()
    with open(PORTFOLIO_PATH, "w") as f:
        json.dump(portfolio, f, indent=2)
    logger.info("Portfolio saved.")


def calculate_position_size(portfolio, target_price):
    """
    Calculates how many shares to buy to achieve exactly 25% allocation.

    Args:
        portfolio: Current portfolio dict
        target_price: The current price of the stock

    Returns:
        Number of shares to buy (int), or 0 if insufficient funds.
    """
    total_value = portfolio.get("cash_balance", 0)

    # Add current holdings value
    for h in portfolio.get("holdings", []):
        total_value += h.get("value", 0)

    # 25% of total portfolio
    target_value = total_value * MAX_ALLOCATION_PCT

    # Calculate brokerage
    brokerage = calculate_brokerage(target_value)

    # Available for shares (after brokerage)
    available = target_value - brokerage

    if available <= 0 or target_price <= 0:
        return 0

    # Number of shares (rounded down to whole number)
    quantity = int(available / target_price)

    # Verify we have enough cash for the trade
    total_cost = (quantity * target_price) + brokerage
    if total_cost > portfolio.get("cash_balance", 0):
        # Reduce quantity to fit within cash
        available_cash = portfolio.get("cash_balance", 0) - brokerage
        quantity = int(available_cash / target_price)

    logger.info(f"Position size: {quantity} shares @ ${target_price:.4f} = "
                f"${quantity * target_price:,.2f} + ${brokerage:.2f} brokerage")
    return max(0, quantity)


def calculate_brokerage(order_value):
    """
    Calculates brokerage for an order.
    $15 for orders up to $15,000, else 0.1% of value.
    """
    if order_value <= 15000:
        return BROKERAGE_FLAT
    return order_value * BROKERAGE_PCT


def can_sell(holding):
    """
    Checks if a holding has met the 1-day minimum holding period.

    Args:
        holding: dict with "purchased_at" timestamp

    Returns:
        True if the holding can be sold.
    """
    purchased_at = holding.get("purchased_at")
    if not purchased_at:
        return True  # If we don't know when it was purchased, allow selling

    purchase_date = datetime.fromisoformat(purchased_at).date()
    today = datetime.now().date()

    # Must have been held for at least 1 full trading day
    return (today - purchase_date).days >= MIN_HOLDING_DAYS


def get_holdings_to_sell(portfolio, signals):
    """
    Determines which current holdings should be sold based on the
    watchlist_rules.md directive.

    Sell conditions:
      1. Not in top signals: replaced by a better candidate
      2. V3 Volume Standoff Exit: Intraday live price drops below benchmark VWAP

    Args:
        portfolio: Current portfolio dict
        signals: List of current momentum signals from the scanner

    Returns:
        List of holding dicts that should be sold.
    """
    to_sell = []
    signal_codes = {s["asx_code"] for s in signals}

    for holding in portfolio.get("holdings", []):
        code = holding.get("code", "")

        # Check holding period
        if not can_sell(holding):
            logger.info(f"Cannot sell {code} yet (holding period not met)")
            continue

        # Check if still in our signal list
        if code not in signal_codes:
            logger.info(f"SELL signal: {code} is no longer in the top momentum list")
            to_sell.append(holding)
            continue
            
        # V3 Advanced Volume Standoff Exit
        try:
            from execution.asx_scanner import get_stock_data, calculate_vwap_distance
            df = get_stock_data(code + ".AX", period="5d")
            if df is not None and not df.empty:
                vwap_dist, _ = calculate_vwap_distance(df)
                if vwap_dist is not None and vwap_dist < -1.5:
                    logger.warning(f"🚨 SELL signal (Volume Break): {code} has dumped {vwap_dist:.2f}% below VWAP!")
                    to_sell.append(holding)
                    continue
        except Exception as e:
            logger.warning(f"V3 Exit check failed for {code}: {e}")

    return to_sell


def get_stocks_to_buy(portfolio, signals):
    """
    Determines which new stocks to buy based on the current portfolio
    and the momentum signals.

    Logic:
      - Fill up to MAX_HOLDINGS (4) stocks
      - Pick the highest-scored signals not already held

    Args:
        portfolio: Current portfolio dict
        signals: List of momentum signals from the scanner

    Returns:
        List of signal dicts to buy.
    """
    current_codes = {h.get("code", "") for h in portfolio.get("holdings", [])}
    available_slots = MAX_HOLDINGS - len(current_codes)

    if available_slots <= 0:
        logger.info("Portfolio is full (4 holdings). No buy actions needed.")
        return []

    to_buy = []
    for signal in signals:
        if signal["asx_code"] not in current_codes:
            to_buy.append(signal)
            if len(to_buy) >= available_slots:
                break

    logger.info(f"Buy candidates: {[s['asx_code'] for s in to_buy]}")
    return to_buy


def record_buy(portfolio, asx_code, quantity, price):
    """Records a completed buy in the portfolio state."""
    brokerage = calculate_brokerage(quantity * price)
    total_cost = (quantity * price) + brokerage

    portfolio["cash_balance"] -= total_cost
    portfolio["holdings"].append({
        "code": asx_code,
        "quantity": quantity,
        "avg_price": price,
        "current_price": price,
        "value": quantity * price,
        "purchased_at": datetime.now().isoformat(),
        "brokerage_paid": brokerage
    })
    save_portfolio(portfolio)
    logger.info(f"Recorded BUY: {quantity} x {asx_code} @ ${price:.4f} "
                f"(total: ${total_cost:,.2f})")


def record_sell(portfolio, asx_code, quantity, price):
    """Records a completed sell in the portfolio state."""
    brokerage = calculate_brokerage(quantity * price)
    proceeds = (quantity * price) - brokerage

    portfolio["cash_balance"] += proceeds

    # Remove the holding
    portfolio["holdings"] = [
        h for h in portfolio["holdings"] if h.get("code") != asx_code
    ]
    save_portfolio(portfolio)
    logger.info(f"Recorded SELL: {quantity} x {asx_code} @ ${price:.4f} "
                f"(proceeds: ${proceeds:,.2f})")


def is_trade_profitable(buy_price, current_price, quantity):
    """
    Checks if selling at the current price would be profitable after brokerage.
    """
    buy_brokerage = calculate_brokerage(quantity * buy_price)
    sell_brokerage = calculate_brokerage(quantity * current_price)

    total_cost = (quantity * buy_price) + buy_brokerage
    total_proceeds = (quantity * current_price) - sell_brokerage

    return total_proceeds > total_cost


def print_portfolio_summary(portfolio):
    """Prints a formatted portfolio summary to console."""
    print(f"\n{'='*50}")
    print(f"  PORTFOLIO SUMMARY")
    print(f"{'='*50}")
    print(f"  Cash:          ${portfolio.get('cash_balance', 0):>12,.2f}")

    holdings = portfolio.get("holdings", [])
    total_shares = sum(h.get("value", 0) for h in holdings)
    print(f"  Shares Value:  ${total_shares:>12,.2f}")
    print(f"  Total:         ${portfolio.get('cash_balance', 0) + total_shares:>12,.2f}")
    print(f"  Holdings:      {len(holdings)}")

    if holdings:
        print(f"\n  {'Code':<6} {'Qty':>8} {'Avg$':>8} {'Cur$':>8} {'Value':>10} {'P&L':>8}")
        print(f"  {'-'*48}")
        for h in holdings:
            qty = h.get("quantity", 0)
            avg = h.get("avg_price", 0)
            cur = h.get("current_price", avg)
            value = qty * cur
            pnl = ((cur - avg) / avg * 100) if avg > 0 else 0
            print(f"  {h.get('code', '?'):<6} {qty:>8} {avg:>8.4f} {cur:>8.4f} ${value:>9,.2f} {pnl:>+7.1f}%")

    print(f"{'='*50}\n")


if __name__ == "__main__":
    portfolio = load_portfolio()
    print_portfolio_summary(portfolio)
