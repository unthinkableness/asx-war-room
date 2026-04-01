"""
Dashboard API Server (Layer 3 Execution)
==========================================
A lightweight HTTP server that serves the War Room dashboard and
exposes JSON API endpoints for the frontend.

Uses Python's built-in http.server — no Flask/FastAPI required.

Endpoints:
    GET  /              → Serves dashboard/index.html
    GET  /api/scan      → Returns data/latest_scan.json
    GET  /api/sentiment → Returns data/latest_sentiment.json
    GET  /api/portfolio → Returns data/portfolio.json
    GET  /api/equity    → Returns data/equity_history.json
    GET  /api/reports   → Returns list of daily reports
    POST /api/run-scan  → Triggers a fresh scan (async)
"""

import os
import sys
import json
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from utils.logger import setup_logger

logger = setup_logger("DashboardAPI")

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DASHBOARD_DIR = os.path.join(PROJECT_ROOT, "dashboard")

# Default port
DEFAULT_PORT = 8050


class DashboardHandler(SimpleHTTPRequestHandler):
    """
    Custom request handler that routes API requests and serves static files.
    """

    def __init__(self, *args, **kwargs):
        # Serve files from the dashboard directory by default
        super().__init__(*args, directory=DASHBOARD_DIR, **kwargs)

    def _check_auth(self):
        """Validates the request password against the environment/config."""
        # For preflight OPTIONS requests, we don't auth
        if self.command == 'OPTIONS':
            return True
            
        auth_header = self.headers.get("X-Hunter-Auth", "")
        expected = os.getenv("API_PASSWORD", "26stowerm")
        
        # When served locally on 8050 without tunneling, we might want to bypass auth? 
        # Actually, let's keep it uniform for security.
        
        if not auth_header or auth_header != expected:
            self._send_json({"error": "Unauthorized. Password required.", "auth_failed": True}, status=401)
            return False
        return True

    def do_OPTIONS(self):
        """Handle preflight requests for CORS."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'X-Requested-With, Content-Type, X-Hunter-Auth')
        self.end_headers()

    def do_GET(self):
        """Handle GET requests."""
        if self.path == '/':
            return self._serve_dashboard()

        # Handle CORS preflight explicitly if needed, but simple GETs are usually fine.
        
        parsed = urlparse(self.path)
        path = parsed.path

        # Require auth for specific active routes
        if path.startswith('/api/'):
            if not self._check_auth():
                return

        if path == '/api/scan':
            self._serve_json_file("latest_scan.json")
        elif path == '/api/sentiment':
            self._serve_json_file("latest_sentiment.json")
        elif path == '/api/portfolio':
            self._serve_json_file("portfolio.json")
        elif path == '/api/equity':
            self._serve_json_file("equity_history.json")
        elif path == '/api/reports':
            self._serve_reports_list()
        elif path == '/api/status':
            self._serve_json_file("system_state.json")
        elif path == '/api/explain':
            query = parse_qs(parsed.query)
            ticker = query.get("ticker", [""])[0]
            self._handle_explain(ticker)
        else:
            super().do_GET()

    def do_POST(self):
        """Handle POST requests."""
        if not self._check_auth():
            return
            
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/api/toggle-automation':
            self._toggle_automation()
        elif path == '/api/buy':
            self._handle_manual_trade('buy')
        elif path == '/api/sell':
            self._handle_manual_trade('sell')
        else:
            self.send_error(404, "Not Found")

    # ─── API Helpers ───

    def _serve_json_file(self, filename):
        """Reads a JSON file from the data directory and returns it."""
        filepath = os.path.join(DATA_DIR, filename)

        if not os.path.exists(filepath):
            self._send_json([], 200)
            return

        try:
            with open(filepath, "r") as f:
                data = json.load(f)
            self._send_json(data)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error reading {filename}: {e}")
            self._send_json({"error": str(e)}, 500)

    def _serve_dashboard(self):
        """Serves the main dashboard HTML file."""
        index_path = os.path.join(DASHBOARD_DIR, "index.html")

        if not os.path.exists(index_path):
            self.send_error(404, "Dashboard not found. Run from the project root.")
            return

        with open(index_path, "rb") as f:
            content = f.read()

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def _serve_reports_list(self):
        """Returns a list of available daily report files."""
        reports = []
        if os.path.exists(DATA_DIR):
            for f in sorted(os.listdir(DATA_DIR), reverse=True):
                if f.startswith("report_") and f.endswith(".json"):
                    filepath = os.path.join(DATA_DIR, f)
                    reports.append({
                        "filename": f,
                        "date": f.replace("report_", "").replace(".json", ""),
                        "size_bytes": os.path.getsize(filepath)
                    })
        self._send_json(reports)

    def _trigger_scan(self):
        """
        Triggers an async momentum scan.
        Runs the scan in a background thread so the API returns immediately.
        """
        logger.info("Scan triggered from dashboard.")

        def run_scan_thread():
            try:
                from execution.asx_scanner import get_asx_momentum_list
                from execution.hotcopper_scraper import batch_sentiment

                # Run momentum scan
                signals = get_asx_momentum_list(top_n=10)

                # Run sentiment on top signals
                if signals:
                    codes = [s["asx_code"] for s in signals]
                    batch_sentiment(codes)

                logger.info(f"Dashboard scan complete. {len(signals)} signals found.")
            except Exception as e:
                logger.error(f"Dashboard scan failed: {e}")

        thread = threading.Thread(target=run_scan_thread, daemon=True)
        thread.start()

        self._send_json({"status": "started", "message": "Scan started in background."})

    def _handle_explain(self, ticker):
        if not ticker:
            self._send_json({"error": "No ticker provided."}, 400)
            return
        
        try:
            from execution.ai_explainer import generate_ai_explanation
            explanation = generate_ai_explanation(ticker)
            self._send_json({"explanation": explanation})
        except Exception as e:
            logger.error(f"Error generating AI explanation: {e}")
            self._send_json({"error": str(e)}, 500)

    def _toggle_automation(self):
        filepath = os.path.join(DATA_DIR, "system_state.json")
        current_state = {"automation_enabled": False}
        
        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    current_state = json.load(f)
            except:
                pass
                
        # Toggle
        new_state = not current_state.get("automation_enabled", False)
        current_state["automation_enabled"] = new_state
        
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(current_state, f, indent=2)
            
        logger.info(f"Automation manually toggled. New state: {new_state}")
        self._send_json({"status": "success", "automation_enabled": new_state})

    def _handle_manual_trade(self, action):
        """Handle manual buy or sell requests."""
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            self._send_json({"error": "Missing payload"}, 400)
            return

        body = self.rfile.read(content_length)
        
        try:
            payload = json.loads(body)
            ticker = payload.get('ticker')
            if not ticker:
                self._send_json({"error": "Missing ticker"}, 400)
                return

            from execution.game_bot import ASXGameBot
            from execution.portfolio_manager import update_portfolio_holdings
            
            bot = ASXGameBot()
            bot.login()
            
            if action == 'buy':
                logger.info(f"Manual BUY triggered for {ticker}")
                amount = float(payload.get('amount', 12500)) # Default $12.5k
                success = bot.buy_stock(ticker, amount)
            else:
                logger.info(f"Manual SELL triggered for {ticker}")
                success = bot.sell_stock(ticker)
                
            bot.close()
            
            # Sync portfolio map
            update_portfolio_holdings()
            
            if success:
                self._send_json({"status": "success", "message": f"{action.upper()} completed for {ticker}"})
            else:
                self._send_json({"error": "Trade execution failed. Check orchestrator logs."}, 500)
                
        except Exception as e:
            logger.error(f"Manual trade failed: {e}")
            self._send_json({"error": str(e)}, 500)

    def _send_json(self, data, status=200):
        """Sends a JSON response."""
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """Override to use our logger instead of stderr."""
        logger.info(f"{self.client_address[0]} - {format % args}")


def start_server(port=DEFAULT_PORT):
    """Starts the dashboard server."""
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    logger.info(f"Dashboard server starting on 0.0.0.0:{port}")
    logger.info(f"Serving dashboard from: {DASHBOARD_DIR}")
    logger.info(f"Reading data from: {DATA_DIR}")
    print(f"\n{'=' * 50}")
    print(f"  ASX MOMENTUM HUNTER — WAR ROOM (CLOUD)")
    print(f"  Port: {port} (Bound to 0.0.0.0)")
    print(f"  Data Volume: {DATA_DIR}")
    print(f"{'=' * 50}\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped by user.")
        server.shutdown()


if __name__ == "__main__":
    # In cloud environments, the PORT is often provided as an environment variable
    env_port = os.getenv("PORT")
    port = int(env_port) if env_port else DEFAULT_PORT
    
    start_server(port=port)
