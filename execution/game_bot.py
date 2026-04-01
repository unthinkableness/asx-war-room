"""
ASX Game Bot (Layer 3 Execution)
==================================
Playwright-based UI automation for the ASX Sharemarket Game portal.

Handles:
  - Login / session management
  - Placing BUY and SELL orders
  - Retrieving portfolio holdings and cash balance
  - Setting "Falling Sell" (trailing stop loss)

The bot uses the JSF-based game portal at:
  https://game.asx.com.au/game/student/school/2026-1/login

Key DOM selectors (from reconnaissance):
  Login:
    - Login ID:   #studentLoginForm\\:loginId
    - Password:   #studentLoginForm\\:password
    - Login Btn:  a.btn-primary (text "Login")
  Trading:
    - Buy radio:    input[value="buy"] or label containing "Buy"
    - Sell radio:   input[value="sell"] or label containing "Sell"
    - ASX Code:     #asxCode (buy), #sellAsxCode (sell)
    - Volume:       #volume
    - Submit:       #submitBtn
"""

import os
import sys
import json
import time
import random
from datetime import datetime

# Playwright import (sync API for simplicity)
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.logger import setup_logger

logger = setup_logger("GameBot")

# Load credentials from .env
from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(env_path)

LOGIN_URL = os.getenv("ASX_GAME_LOGIN_URL", "https://game.asx.com.au/game/student/school/2026-1/login")
BASE_URL = os.getenv("ASX_GAME_BASE_URL", "https://game.asx.com.au/game/play/school/2026-1")
LOGIN_ID = os.getenv("ASX_GAME_LOGIN_ID")
PASSWORD = os.getenv("ASX_GAME_PASSWORD")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# Page URLs
DASHBOARD_URL = BASE_URL
ORDER_URL = f"{BASE_URL}/orders/add"
PORTFOLIO_URL = f"{BASE_URL}/portfolio"
WATCHLIST_URL = f"{BASE_URL}/watchlist"
TRANSACTIONS_URL = f"{BASE_URL}/transactions"


def human_delay(min_ms=500, max_ms=2000):
    """Random delay to mimic human browsing patterns."""
    delay = random.randint(min_ms, max_ms) / 1000.0
    time.sleep(delay)


class ASXGameBot:
    """
    Manages a Playwright browser session against the ASX Game portal.
    """

    def __init__(self, headless=True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.page = None
        self.logged_in = False

    def start(self):
        """Launch the browser and create a new page."""
        logger.info(f"Starting browser (headless={self.headless})...")
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        self.page = self.browser.new_page()
        self.page.set_default_timeout(30000)
        logger.info("Browser started.")

    def stop(self):
        """Close the browser and cleanup."""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        self.logged_in = False
        logger.info("Browser closed.")

    def login(self):
        """
        Navigates to the login page and authenticates.
        The game uses JSF forms, so we interact with the DOM directly.
        """
        if not LOGIN_ID or not PASSWORD:
            raise ValueError("ASX Game credentials not found in .env file!")

        logger.info(f"Navigating to login page: {LOGIN_URL}")
        self.page.goto(LOGIN_URL)
        self.page.wait_for_load_state("networkidle")
        human_delay(1000, 2000)

        # Fill credentials
        logger.info(f"Entering Login ID: {LOGIN_ID}")
        login_field = self.page.locator("#studentLoginForm\\:loginId")
        login_field.fill(LOGIN_ID)
        human_delay(300, 800)

        logger.info("Entering password...")
        password_field = self.page.locator("#studentLoginForm\\:password")
        password_field.fill(PASSWORD)
        human_delay(300, 800)

        # Click Login button
        logger.info("Clicking Login button...")
        login_btn = self.page.locator("a.btn-primary:has-text('Login')")
        login_btn.click()

        # Wait for navigation to dashboard
        try:
            self.page.wait_for_url(f"**/game/play/**", timeout=15000)
            self.logged_in = True
            logger.info("Login successful! Redirected to dashboard.")
        except PwTimeout:
            logger.error("Login failed! Did not redirect to dashboard.")
            # Check for error messages
            error_el = self.page.locator(".error-message, .alert-danger")
            if error_el.count() > 0:
                logger.error(f"Login error: {error_el.first.text_content()}")
            raise RuntimeError("Login to ASX Game failed.")

    def get_portfolio(self):
        """
        Navigates to the portfolio page and extracts current holdings.

        Returns a dict:
        {
            "cash": 50000.00,
            "shares_value": 0.00,
            "total_value": 50000.00,
            "holdings": [
                {"code": "BRN", "quantity": 1000, "avg_price": 0.45, "current_price": 0.50, "value": 500.00, "gain_pct": 11.1}
            ],
            "retrieved_at": "2026-04-01T15:00:00"
        }
        """
        self._ensure_logged_in()

        logger.info(f"Navigating to portfolio: {PORTFOLIO_URL}")
        self.page.goto(PORTFOLIO_URL)
        self.page.wait_for_load_state("networkidle")
        human_delay(1000, 2000)

        portfolio = {
            "cash": 0.0,
            "shares_value": 0.0,
            "total_value": 0.0,
            "holdings": [],
            "retrieved_at": datetime.now().isoformat()
        }

        # Extract cash summary from the account summary section
        try:
            # The dashboard shows "Cash: $50,000.00"
            cash_text = self.page.locator("text=Cash").first.text_content()
            cash_match = _extract_dollar_amount(cash_text)
            if cash_match:
                portfolio["cash"] = cash_match

            total_text = self.page.locator("text=Total Portfolio Value").first.text_content()
            total_match = _extract_dollar_amount(total_text)
            if total_match:
                portfolio["total_value"] = total_match

            shares_text = self.page.locator("text=Shares").first.text_content()
            shares_match = _extract_dollar_amount(shares_text)
            if shares_match:
                portfolio["shares_value"] = shares_match
        except Exception as e:
            logger.warning(f"Could not parse portfolio summary: {e}")

        # Extract individual holdings from the portfolio table
        try:
            rows = self.page.locator("table tbody tr")
            count = rows.count()
            for i in range(count):
                row = rows.nth(i)
                cells = row.locator("td")
                if cells.count() >= 4:
                    holding = {
                        "code": cells.nth(0).text_content().strip(),
                        "quantity": _parse_number(cells.nth(1).text_content()),
                        "avg_price": _parse_number(cells.nth(2).text_content()),
                        "current_price": _parse_number(cells.nth(3).text_content()),
                    }
                    if holding["quantity"] and holding["current_price"]:
                        holding["value"] = round(holding["quantity"] * holding["current_price"], 2)
                    holdings = portfolio["holdings"]
                    holdings.append(holding)
        except Exception as e:
            logger.warning(f"Could not parse holdings table: {e}")

        logger.info(f"Portfolio: Cash=${portfolio['cash']:,.2f}, "
                    f"Shares=${portfolio['shares_value']:,.2f}, "
                    f"Total=${portfolio['total_value']:,.2f}, "
                    f"Holdings={len(portfolio['holdings'])}")

        # Save to data dir
        _save_json(portfolio, "portfolio.json")
        return portfolio

    def place_order(self, asx_code, quantity, order_type="buy"):
        """
        Places a BUY or SELL order on the ASX Game portal.

        Args:
            asx_code: The ASX ticker code (e.g., "BRN")
            quantity: Number of shares to buy/sell
            order_type: "buy" or "sell"

        Returns:
            True on success, False on failure.
        """
        self._ensure_logged_in()

        if DRY_RUN:
            logger.info(f"[DRY RUN] Would {order_type.upper()} {quantity} x {asx_code}")
            _log_trade(asx_code, quantity, order_type, "dry_run")
            return True

        logger.info(f"Placing {order_type.upper()} order: {quantity} x {asx_code}")
        self.page.goto(ORDER_URL)
        self.page.wait_for_load_state("networkidle")
        human_delay(1000, 2000)

        try:
            # 1. Select Buy or Sell
            if order_type.lower() == "buy":
                self.page.locator("label:has-text('Buy')").click()
                human_delay(500, 1000)

                # 2. Select the ASX code from the dropdown
                code_dropdown = self.page.locator("#asxCode")
                code_dropdown.select_option(label=asx_code)
            elif order_type.lower() == "sell":
                self.page.locator("label:has-text('Sell')").click()
                human_delay(500, 1000)

                code_dropdown = self.page.locator("#sellAsxCode")
                code_dropdown.select_option(label=asx_code)
            else:
                raise ValueError(f"Invalid order type: {order_type}")

            human_delay(500, 1000)

            # 3. Enter volume
            volume_field = self.page.locator("#volume")
            volume_field.fill(str(int(quantity)))
            human_delay(300, 800)

            # 4. Submit the order
            submit_btn = self.page.locator("#submitBtn")
            submit_btn.click()
            human_delay(2000, 4000)

            # 5. Check for confirmation or error
            # Look for success indicators
            page_text = self.page.content()
            if "order has been placed" in page_text.lower() or "confirmation" in page_text.lower():
                logger.info(f"Order confirmed: {order_type.upper()} {quantity} x {asx_code}")
                _log_trade(asx_code, quantity, order_type, "confirmed")
                return True
            else:
                # Check for error messages
                error_el = self.page.locator(".error, .alert-danger, .errorMessage")
                if error_el.count() > 0:
                    error_text = error_el.first.text_content()
                    logger.error(f"Order error: {error_text}")
                    _log_trade(asx_code, quantity, order_type, f"error: {error_text}")
                    return False
                else:
                    # May need to confirm on a second page
                    confirm_btn = self.page.locator("button:has-text('Confirm'), a:has-text('Confirm')")
                    if confirm_btn.count() > 0:
                        confirm_btn.first.click()
                        human_delay(2000, 3000)
                        logger.info(f"Order confirmed (2-step): {order_type.upper()} {quantity} x {asx_code}")
                        _log_trade(asx_code, quantity, order_type, "confirmed")
                        return True

                    logger.warning(f"Order status unclear for {asx_code}")
                    _log_trade(asx_code, quantity, order_type, "unclear")
                    return False

        except Exception as e:
            logger.error(f"Error placing order for {asx_code}: {e}")
            _log_trade(asx_code, quantity, order_type, f"exception: {str(e)}")
            return False

    def get_cash_balance(self):
        """Quick method to get just the cash balance from the dashboard."""
        self._ensure_logged_in()

        self.page.goto(DASHBOARD_URL)
        self.page.wait_for_load_state("networkidle")
        human_delay(1000, 2000)

        try:
            cash_el = self.page.locator("text=Cash").first
            cash_text = cash_el.text_content()
            return _extract_dollar_amount(cash_text)
        except Exception as e:
            logger.warning(f"Could not read cash balance: {e}")
            return None

    def _ensure_logged_in(self):
        """Check if we're logged in, login if not."""
        if not self.logged_in:
            self.login()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _extract_dollar_amount(text):
    """Extracts a dollar amount from text like 'Cash: $50,000.00'"""
    import re
    match = re.search(r'\$?([\d,]+\.?\d*)', text)
    if match:
        return float(match.group(1).replace(",", ""))
    return None


def _parse_number(text):
    """Parses a number from text, handling commas and dollar signs."""
    if not text:
        return None
    import re
    cleaned = re.sub(r'[^\d.\-]', '', text.strip())
    try:
        return float(cleaned) if '.' in cleaned else int(cleaned)
    except ValueError:
        return None


def _save_json(data, filename):
    """Saves data to the project's data directory."""
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(data_dir, exist_ok=True)
    filepath = os.path.join(data_dir, filename)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)


def _log_trade(asx_code, quantity, order_type, status):
    """Appends a trade record to the trade log CSV."""
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(data_dir, exist_ok=True)
    log_path = os.path.join(data_dir, "trade_log.csv")

    header_needed = not os.path.exists(log_path)
    with open(log_path, "a") as f:
        if header_needed:
            f.write("timestamp,asx_code,quantity,order_type,status\n")
        f.write(f"{datetime.now().isoformat()},{asx_code},{quantity},{order_type},{status}\n")


# ---------------------------------------------------------------------------
# Convenience functions (called by orchestrator)
# ---------------------------------------------------------------------------

def place_buy_order(asx_code, quantity, headless=True):
    """One-shot buy order. Opens browser, logs in, buys, closes."""
    bot = ASXGameBot(headless=headless)
    try:
        bot.start()
        bot.login()
        result = bot.place_order(asx_code, quantity, order_type="buy")
        return result
    finally:
        bot.stop()


def place_sell_order(asx_code, quantity, headless=True):
    """One-shot sell order. Opens browser, logs in, sells, closes."""
    bot = ASXGameBot(headless=headless)
    try:
        bot.start()
        bot.login()
        result = bot.place_order(asx_code, quantity, order_type="sell")
        return result
    finally:
        bot.stop()


def fetch_portfolio(headless=True):
    """One-shot: logs in, fetches portfolio, closes."""
    bot = ASXGameBot(headless=headless)
    try:
        bot.start()
        bot.login()
        return bot.get_portfolio()
    finally:
        bot.stop()


if __name__ == "__main__":
    print("=" * 60)
    print("ASX GAME BOT")
    print("=" * 60)
    print(f"DRY_RUN: {DRY_RUN}")
    print(f"LOGIN_URL: {LOGIN_URL}")
    print()

    # Demo: Fetch portfolio
    bot = ASXGameBot(headless=False)
    try:
        bot.start()
        bot.login()
        portfolio = bot.get_portfolio()
        print(json.dumps(portfolio, indent=2))
    finally:
        bot.stop()
