# ASX Game: Momentum Hunter Automation

An automated trading framework designed to win the ASX Sharemarket Game using a high-risk, high-concentration strategy.

## 📁 System Architecture (3-Layer)

- **Layer 1: Directive (SOPs)**
  - `directives/trading_parameters.md`: High-risk entry/exit logic.
  - `directives/watchlist_rules.md`: Portfolio rotation rules.
- **Layer 2: Orchestration (Brain)**
  - `orchestrator.py`: The main decision engine.
- **Layer 3: Execution (Tools)**
  - `execution/asx_scanner.py`: Price/volume data ingestion.
  - `execution/game_bot.py`: Selenium/Playwright UI driver.
  - `execution/hotcopper_scraper.py`: Sentiment analysis.
- **Infrastructure**
  - `utils/logger.py`: Centralized logging.
  - `data/portfolio.json`: Persistent state for cash and holdings.
  - `requirements.txt`: Project dependencies.
  - `tests/`: Unit testing suite.

## 🚀 Strategy: The "20-Min Gap"

This system is built to exploit the 20-minute price delay in the ASX Sharemarket Game dashboard by using live market data (previewing the future) to place orders before the game catches up.

> [!WARNING]
> Use of this system may violate the ASX Sharemarket Game Terms of Service. This is a conceptual and structural framework only.
