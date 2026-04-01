"""
ASX Momentum Hunter — Dashboard Launcher
==========================================
Convenience entry point: starts the dashboard API server and
optionally opens the browser.

Usage:
    python dashboard.py              # Start on default port 8050
    python dashboard.py --port 9000  # Start on custom port
    python dashboard.py --no-browser # Don't auto-open browser
"""

import os
import sys
import argparse
import webbrowser
import time
import threading

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)


def main():
    parser = argparse.ArgumentParser(description="ASX Momentum Hunter Dashboard")
    parser.add_argument("--port", type=int, default=8050, help="Port to listen on (default: 8050)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = parser.parse_args()

    # Ensure data directory exists with defaults
    data_dir = os.path.join(PROJECT_ROOT, "data")
    os.makedirs(data_dir, exist_ok=True)

    # Seed equity_history.json if it doesn't exist
    equity_path = os.path.join(data_dir, "equity_history.json")
    if not os.path.exists(equity_path):
        import json
        from datetime import datetime
        with open(equity_path, "w") as f:
            json.dump([{
                "date": datetime.now().isoformat(),
                "total_value": 50000.0,
                "cash": 50000.0,
                "holdings_count": 0
            }], f, indent=2)

    # Auto-open browser after a short delay
    if not args.no_browser:
        def open_browser():
            time.sleep(1.5)
            url = f"http://localhost:{args.port}"
            print(f"  Opening browser: {url}")
            webbrowser.open(url)

        threading.Thread(target=open_browser, daemon=True).start()

    # Start the server
    from execution.dashboard_api import start_server
    start_server(port=args.port)


if __name__ == "__main__":
    main()
