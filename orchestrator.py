"""
ASX Game Orchestrator (Layer 2 Decision Engine) — v2
====================================================
The "Brain" of the automation system.

This is the central orchestration script that:
  1. Runs the ASX momentum scanner to find high-volatility candidates
  2. Scores candidates with NLP-powered sentiment analysis
  3. Checks the current portfolio for stale holdings to sell
  4. Calculates position sizes (25% allocation)
  5. Executes trades via the Game Bot
  6. Tracks equity history for the dashboard

Usage:
  python orchestrator.py              # Full daily run (DRY_RUN mode by default)
  python orchestrator.py --scan       # Scan only (no trades)
  python orchestrator.py --portfolio  # Show portfolio only
  python orchestrator.py --live       # Override DRY_RUN and execute real trades
"""

import os
import sys
import json
import argparse
from datetime import datetime

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from utils.logger import setup_logger
from execution.asx_scanner import get_asx_momentum_list, get_live_price
from execution.hotcopper_scraper import get_ticker_sentiment
from execution.portfolio_manager import (
    load_portfolio, save_portfolio, calculate_position_size,
    get_holdings_to_sell, get_stocks_to_buy,
    record_buy, record_sell, print_portfolio_summary
)
from execution.game_bot import ASXGameBot

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
except ImportError:
    pass # Dotenv not required in GitHub Actions

logger = setup_logger("Orchestrator")

# Sentiment threshold from directive: trading_parameters.md
SENTIMENT_THRESHOLD = 0.7


def run_scan(top_n=10):
    """
    Phase 1: Scan for momentum candidates.
    Returns the top N signals sorted by composite score.
    """
    logger.info("=" * 60)
    logger.info("PHASE 1: MOMENTUM SCAN")
    logger.info("=" * 60)

    signals = get_asx_momentum_list(top_n=top_n)

    if not signals:
        logger.warning("No momentum signals found. Market may be flat or closed.")
        return []

    logger.info(f"Found {len(signals)} momentum candidates:")
    for i, s in enumerate(signals):
        logger.info(f"  {i+1}. {s['asx_code']} | Score={s['score']} | "
                     f"RVol={s['rvol']}x | ATR%={s['atr_pct']}%")
    return signals


def run_sentiment_filter(signals, threshold=SENTIMENT_THRESHOLD):
    """
    Phase 2: Filter candidates by sentiment.
    Only keep stocks with sentiment score above the threshold.
    """
    logger.info("=" * 60)
    logger.info("PHASE 2: SENTIMENT FILTER")
    logger.info("=" * 60)

    if not signals:
        return []

    filtered = []
    for signal in signals:
        code = signal["asx_code"]
        sentiment = get_ticker_sentiment(code)

        signal["sentiment_score"] = sentiment["sentiment_score"]
        signal["sentiment_posts"] = sentiment["post_count"]

        if sentiment["sentiment_score"] >= threshold:
            logger.info(f"  ✓ {code}: sentiment={sentiment['sentiment_score']:.3f} (PASS)")
            filtered.append(signal)
        else:
            logger.info(f"  ✗ {code}: sentiment={sentiment['sentiment_score']:.3f} (FAIL, "
                         f"need ≥{threshold})")

    # Re-sort by combined score: momentum * sentiment
    for s in filtered:
        s["combined_score"] = round(s["score"] * s["sentiment_score"], 2)
    filtered.sort(key=lambda x: x["combined_score"], reverse=True)

    logger.info(f"Sentiment filter: {len(filtered)}/{len(signals)} passed")
    return filtered


def run_portfolio_decisions(signals):
    """
    Phase 3: Make buy/sell decisions.
    Returns (to_sell, to_buy) lists.
    """
    logger.info("=" * 60)
    logger.info("PHASE 3: PORTFOLIO DECISIONS")
    logger.info("=" * 60)

    portfolio = load_portfolio()
    print_portfolio_summary(portfolio)

    # Determine sells
    to_sell = get_holdings_to_sell(portfolio, signals)
    if to_sell:
        logger.info(f"SELL candidates: {[h['code'] for h in to_sell]}")
    else:
        logger.info("No holdings flagged for selling.")

    # Determine buys (after accounting for sells)
    # Simulate sells first to know available slots
    simulated_portfolio = portfolio.copy()
    simulated_portfolio["holdings"] = [
        h for h in portfolio["holdings"]
        if h not in to_sell
    ]

    to_buy = get_stocks_to_buy(simulated_portfolio, signals)
    if to_buy:
        logger.info(f"BUY candidates: {[s['asx_code'] for s in to_buy]}")
    else:
        logger.info("No buy actions needed.")

    return to_sell, to_buy


def execute_trades(to_sell, to_buy, force_live=False):
    """
    Phase 4: Execute trades via the Game Bot.
    """
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    if force_live:
        dry_run = False

    # Check automation toggle
    try:
        state_path = os.path.join(PROJECT_ROOT, "data", "system_state.json")
        with open(state_path, "r") as f:
            state = json.load(f)
            if not state.get("automation_enabled", True) and not force_live:
                logger.warning("🚫 Automation is currently disabled via the dashboard kill switch. "
                               "Execution phase skipped.")
                return
    except Exception:
        pass

    logger.info("=" * 60)
    logger.info(f"PHASE 4: TRADE EXECUTION {'(DRY RUN)' if dry_run else '(LIVE)'}")
    logger.info("=" * 60)

    if not to_sell and not to_buy:
        logger.info("No trades to execute.")
        return

    portfolio = load_portfolio()

    if dry_run:
        # Log what would happen
        for holding in to_sell:
            logger.info(f"[DRY RUN] SELL {holding['quantity']} x {holding['code']}")
            record_sell(portfolio, holding["code"], holding["quantity"],
                        holding.get("current_price", holding.get("avg_price", 0)))

        for signal in to_buy:
            price = signal.get("price", 0)
            qty = calculate_position_size(portfolio, price)
            if qty > 0:
                logger.info(f"[DRY RUN] BUY {qty} x {signal['asx_code']} @ ${price:.4f}")
                record_buy(portfolio, signal["asx_code"], qty, price)
            else:
                logger.info(f"[DRY RUN] Skipping {signal['asx_code']} (insufficient funds)")
        return

    # LIVE EXECUTION
    bot = ASXGameBot(headless=True)
    try:
        bot.start()
        bot.login()

        # Execute sells first (to free up cash)
        for holding in to_sell:
            code = holding["code"]
            qty = holding["quantity"]
            logger.info(f"EXECUTING SELL: {qty} x {code}")

            success = bot.place_order(code, qty, order_type="sell")
            if success:
                record_sell(portfolio, code, qty,
                            holding.get("current_price", holding.get("avg_price", 0)))
            else:
                logger.error(f"SELL FAILED for {code}")

        # Execute buys
        # Refresh portfolio after sells
        portfolio = load_portfolio()

        for signal in to_buy:
            code = signal["asx_code"]
            price = get_live_price(signal["ticker"]) or signal.get("price", 0)
            qty = calculate_position_size(portfolio, price)

            if qty <= 0:
                logger.warning(f"Skipping BUY for {code} (insufficient funds or invalid price)")
                continue

            logger.info(f"EXECUTING BUY: {qty} x {code} @ ~${price:.4f}")
            success = bot.place_order(code, qty, order_type="buy")
            if success:
                record_buy(portfolio, code, qty, price)
            else:
                logger.error(f"BUY FAILED for {code}")

    finally:
        bot.stop()


def save_daily_report(signals, to_sell, to_buy):
    """Saves a daily report to the data directory."""
    data_dir = os.path.join(PROJECT_ROOT, "data")
    os.makedirs(data_dir, exist_ok=True)

    report = {
        "date": datetime.now().isoformat(),
        "signals": signals,
        "sell_orders": [h.get("code", "?") for h in to_sell],
        "buy_orders": [s.get("asx_code", "?") for s in to_buy],
        "portfolio": load_portfolio()
    }

    report_path = os.path.join(data_dir, f"report_{datetime.now().strftime('%Y%m%d')}.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    logger.info(f"Daily report saved to {report_path}")


def main(scan_only=False, portfolio_only=False, force_live=False, auto_deploy=False):
    """
    Main orchestration loop.
    """
    logger.info("=" * 60)
    logger.info("ASX MOMENTUM HUNTER - DAILY RUN")
    logger.info(f"Time: {datetime.now().isoformat()}")
    logger.info("=" * 60)

    try:
        # Portfolio-only mode
        if portfolio_only:
            portfolio = load_portfolio()
            print_portfolio_summary(portfolio)
            return

        # Phase 1: Scan
        signals = run_scan(top_n=10)
        if not signals:
            logger.info("No signals found. Market may be flat or closed.")
            return

        # Phase 2: Sentiment filter
        filtered = run_sentiment_filter(signals)

        # Scan-only mode
        if scan_only:
            logger.info("Scan-only mode. No trades will be executed.")
            print(f"\n{'Rank':<5} {'Code':<6} {'Price':>10} {'RVol':>6} {'ATR%':>6} "
                  f"{'VWAP%':>7} {'Gap%':>6} {'Sent.':>6} {'Method':>8} {'Combined':>9}")
            print("-" * 80)
            for i, s in enumerate(filtered):
                gap_flag = "⚡" if s.get('gap_up') else " "
                method_tag = "NLP" if s.get('sentiment_method') == 'finbert' else "KW"
                print(f"{i+1:<5} {s['asx_code']:<6} ${s['price']:>8.4f} "
                      f"{s['rvol']:>5.2f}x {s['atr_pct']:>5.2f}% "
                      f"{s.get('vwap_distance_pct', 0):>+6.1f}% "
                      f"{s.get('gap_pct', 0):>5.1f}%"
                      f"{s.get('sentiment_score', 0):>6.3f} "
                      f"{method_tag:>8} "
                      f"{s.get('combined_score', 0):>8.2f} {gap_flag}")
            return

        # Phase 3: Portfolio decisions
        to_sell, to_buy = run_portfolio_decisions(filtered)

        # Phase 4: Execute
        execute_trades(to_sell, to_buy, force_live=force_live)

        # Save daily report
        save_daily_report(filtered, to_sell, to_buy)

    finally:
        # ALWAYS Track equity history for dashboard, even if early return or error
        save_equity_point()

        # ALWAYS Optional: Deploy to public URL
        if auto_deploy:
            logger.info("Auto-deploying War Room dashboard...")
            try:
                import subprocess
                deploy_script = os.path.join(PROJECT_ROOT, "execution", "deploy_dashboard.py")
                subprocess.run([sys.executable, deploy_script], check=True)
                logger.info("Dashboard deployed successfully.")
            except Exception as e:
                logger.error(f"Auto-deployment failed: {e}")

        # Final summary
        portfolio = load_portfolio()
        print_portfolio_summary(portfolio)

        logger.info("Daily run complete.")


def save_equity_point(portfolio=None):
    """
    Appends the current portfolio total value to the equity history file.
    Used by the dashboard to draw the equity curve.
    """
    if portfolio is None:
        portfolio = load_portfolio()

    total_value = portfolio.get("cash_balance", 0)
    for h in portfolio.get("holdings", []):
        total_value += h.get("value", 0)

    data_dir = os.path.join(PROJECT_ROOT, "data")
    os.makedirs(data_dir, exist_ok=True)
    equity_path = os.path.join(data_dir, "equity_history.json")

    # Load existing history
    history = []
    if os.path.exists(equity_path):
        try:
            with open(equity_path, "r") as f:
                history = json.load(f)
        except (json.JSONDecodeError, IOError):
            history = []

    # Append new data point
    history.append({
        "date": datetime.now().isoformat(),
        "total_value": round(total_value, 2),
        "cash": round(portfolio.get("cash_balance", 0), 2),
        "holdings_count": len(portfolio.get("holdings", []))
    })

    with open(equity_path, "w") as f:
        json.dump(history, f, indent=2)

    logger.info(f"Equity point saved: ${total_value:,.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ASX Momentum Hunter Orchestrator")
    parser.add_argument("--scan", action="store_true", help="Scan only (no trades)")
    parser.add_argument("--portfolio", action="store_true", help="Show portfolio only")
    parser.add_argument("--live", action="store_true", help="Execute real trades (override DRY_RUN)")
    parser.add_argument("--deploy", action="store_true", help="Deploy dashboard to Surge.sh after run")
    parser.add_argument("--loop", action="store_true", help="Run in continuous 24/7 loop mode (cloud default)")
    parser.add_argument("--buy", type=str, help="Manually buy a specific ticker (for manual trades)")
    parser.add_argument("--sell", type=str, help="Manually sell a specific ticker (for manual trades)")
    parser.add_argument("--amount", type=float, default=12500, help="Amount to buy ($)")
    args = parser.parse_args()

    # Special Mode: Manual Buy/Sell
    if args.buy or args.sell:
        force_live = True if os.getenv("DRY_RUN") == "False" else args.live
        ticker = args.buy if args.buy else args.sell
        action = "buy" if args.buy else "sell"
        
        logger.info(f"Manual {action.upper()} triggered for {ticker}")
        
        bot = ASXGameBot()
        bot.login()
        if action == "buy":
            bot.buy_stock(ticker, args.amount)
        else:
            bot.sell_stock(ticker)
        bot.close()
        
        from execution.portfolio_manager import update_portfolio_holdings
        update_portfolio_holdings()
        save_equity_point()
        logger.info("Manual trade execution complete.")
        sys.exit(0)

    if args.loop:
        import time
        from datetime import datetime
        import pytz
        
        AEST = pytz.timezone('Australia/Sydney')
        
        logger.info("Starting Autonomous Cloud Loop...")
        
        while True:
            now = datetime.now(AEST)
            is_weekday = now.weekday() < 5
            is_market_hours = is_weekday and (now.hour >= 10 and (now.hour < 16 or (now.hour == 16 and now.minute <= 15)))
            
            # Check Kill Switch
            filepath = os.path.join(PROJECT_ROOT, "data", "system_state.json")
            automation_enabled = False
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r") as f:
                        state = json.load(f)
                        automation_enabled = state.get("automation_enabled", False)
                except:
                    pass
            
            if not automation_enabled:
                logger.info("Automation is DISABLED via dashboard. Sleeping 60s...")
                time.sleep(60)
                continue

            if is_market_hours:
                logger.info(f"Market is OPEN ({now.strftime('%H:%M')}). Running hunter...")
                try:
                    main(scan_only=False, portfolio_only=False, force_live=args.live, auto_deploy=False)
                except Exception as e:
                    logger.error(f"Loop execution failed: {e}")
                
                logger.info("Execution finished. Sleeping 30 minutes...")
                time.sleep(1800) # 30 mins
            else:
                if not is_weekday:
                    logger.info("Market is CLOSED (Weekend). Sleeping 1 hour...")
                    time.sleep(3600)
                else:
                    logger.info(f"Market is CLOSED ({now.strftime('%H:%M')}). Sleeping 15 minutes...")
                    time.sleep(900)
    else:
        main(
            scan_only=args.scan,
            portfolio_only=args.portfolio,
            force_live=args.live,
            auto_deploy=args.deploy
        )
