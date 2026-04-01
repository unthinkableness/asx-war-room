"""
HotCopper Sentiment Scraper (Layer 3 Execution) — v2
=====================================================
Scrapes HotCopper (Australia's largest stock forum) for discussion
frequency and sentiment on ASX tickers.

Returns a sentiment score between 0.0 (cold) and 1.0 (extremely hot).

v1 Methodology:
  - Post count in the last 24h relative to the 7-day average
  - Thread title sentiment (bullish keywords vs bearish keywords)
  - "Likes" / engagement ratio on recent posts

v2 Additions:
  - Social Velocity: posts_recent / avg_posts_per_period. Velocity > 3.0
    means the stock is "trending" before it peaks.
  - NLP Sentiment (FinBERT): Optional upgrade from keyword counting to
    transformer-based financial text classification.
"""

import requests
from bs4 import BeautifulSoup
import re
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.logger import setup_logger

logger = setup_logger("SentimentScraper")

# Keywords for basic sentiment classification
BULLISH_KEYWORDS = [
    "buy", "bullish", "breakout", "rocket", "moon", "surge", "rally",
    "accumulate", "undervalued", "upside", "strong", "positive", "profit",
    "discovery", "approval", "contract", "deal", "upgrade", "production",
    "record", "growth", "drill", "hit", "assay", "high-grade", "bonanza"
]

BEARISH_KEYWORDS = [
    "sell", "bearish", "dump", "crash", "overvalued", "downside", "weak",
    "loss", "dilution", "placement", "risk", "debt", "negative", "warning",
    "downgrade", "suspend", "halt", "fraud", "scam", "avoid", "falling"
]

# Headers to mimic a real browser
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}


def scrape_hotcopper_posts(asx_code, max_pages=2):
    """
    Scrapes HotCopper discussion threads for a given ASX ticker.
    Returns a list of post dicts with title, timestamp, and engagement metrics.

    Note: HotCopper may block automated requests. This function includes
    error handling and fallback to a basic analysis.
    """
    posts = []
    base_url = f"https://hotcopper.com.au/asx/{asx_code.lower()}/"

    for page in range(1, max_pages + 1):
        url = base_url if page == 1 else f"{base_url}?page={page}"
        logger.info(f"Fetching {url}...")

        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            if response.status_code != 200:
                logger.warning(f"HTTP {response.status_code} for {url}")
                break

            soup = BeautifulSoup(response.text, "html.parser")

            # HotCopper thread titles are typically in <a> tags within thread lists
            # The exact selectors may change; this is a best-effort parse
            thread_elements = soup.select("a.thread-title, .threadTitle a, h3.title a")

            if not thread_elements:
                # Fallback: look for any links that contain the ticker
                thread_elements = soup.find_all("a", href=re.compile(r"/threads/"))

            for elem in thread_elements:
                title = elem.get_text(strip=True)
                if not title:
                    continue

                posts.append({
                    "title": title,
                    "url": elem.get("href", ""),
                    "source": "hotcopper"
                })

        except requests.exceptions.RequestException as e:
            logger.warning(f"Request failed for {asx_code}: {e}")
            break

    logger.info(f"Found {len(posts)} posts for {asx_code}")
    return posts


def analyze_sentiment(posts):
    """
    Performs basic keyword-based sentiment analysis on post titles.

    Returns:
      - score: 0.0 to 1.0 (0 = very bearish, 0.5 = neutral, 1.0 = very bullish)
      - bullish_count: number of bullish signals
      - bearish_count: number of bearish signals
    """
    if not posts:
        return 0.5, 0, 0  # Neutral if no data

    bullish_count = 0
    bearish_count = 0

    for post in posts:
        title_lower = post["title"].lower()

        for kw in BULLISH_KEYWORDS:
            if kw in title_lower:
                bullish_count += 1

        for kw in BEARISH_KEYWORDS:
            if kw in title_lower:
                bearish_count += 1

    total = bullish_count + bearish_count
    if total == 0:
        return 0.5, 0, 0  # Neutral

    # Score = bullish ratio, weighted by volume of discussion
    raw_score = bullish_count / total

    # Volume bonus: more posts = more confidence in the signal
    # Cap the bonus at 0.1 for very active stocks
    volume_bonus = min(0.1, len(posts) / 100)

    score = min(1.0, raw_score + volume_bonus)
    return round(score, 3), bullish_count, bearish_count


def analyze_sentiment_nlp(posts):
    """
    v2: Uses FinBERT NLP for sentiment classification on post titles.
    Falls back to keyword analysis if FinBERT is unavailable.

    Returns:
      - score: 0.0 to 1.0
      - method: "finbert" or "keyword"
      - details: list of per-post results
    """
    if not posts:
        return 0.5, "keyword", []

    try:
        from execution.nlp_sentiment import batch_analyze, get_aggregate_score

        titles = [p["title"] for p in posts if p.get("title")]
        if not titles:
            return 0.5, "keyword", []

        results = batch_analyze(titles)
        agg_score = get_aggregate_score(results)
        method = results[0].get("method", "keyword") if results else "keyword"

        return agg_score, method, results
    except ImportError:
        # NLP module not available, fall back
        score, _, _ = analyze_sentiment(posts)
        return score, "keyword", []
    except Exception as e:
        logger.warning(f"NLP sentiment failed, falling back to keywords: {e}")
        score, _, _ = analyze_sentiment(posts)
        return score, "keyword", []


def calculate_social_velocity(posts, recent_count=None, baseline_count=None):
    """
    v2: Calculates social velocity — the acceleration of forum activity.

    A stock that usually gets 5 posts a day suddenly getting 20 posts
    is a massive anomaly signal (velocity = 4.0x).

    Since we can't get exact timestamps from scraping, we use post count
    relative to expected baseline as a proxy.

    Args:
        posts: list of scraped posts (current session)
        recent_count: override for recent post count
        baseline_count: override for baseline post count

    Returns:
        velocity (float): ratio of current activity to baseline.
                         > 3.0 = "trending", > 5.0 = "viral"
    """
    current = recent_count if recent_count is not None else len(posts)

    # Baseline: assume average ASX small-cap gets ~8-12 posts per scrape window
    # This is a heuristic; can be refined with historical data
    baseline = baseline_count if baseline_count is not None else 10

    if baseline <= 0:
        return 1.0

    velocity = current / baseline
    return round(velocity, 2)


def get_ticker_sentiment(asx_code):
    """
    Public API: Returns a sentiment score for a given ASX ticker.
    Called by the orchestrator.

    Returns a dict:
    {
        "asx_code": "BRN",
        "sentiment_score": 0.72,
        "bullish_signals": 15,
        "bearish_signals": 6,
        "post_count": 28,
        "analyzed_at": "2026-04-01T15:00:00"
    }
    """
    logger.info(f"Analyzing sentiment for {asx_code}...")

    posts = scrape_hotcopper_posts(asx_code)

    # v2: Use NLP sentiment if available
    nlp_score, nlp_method, nlp_details = analyze_sentiment_nlp(posts)

    # Also get keyword scores for comparison / fallback
    keyword_score, bullish, bearish = analyze_sentiment(posts)

    # v2: Social velocity
    velocity = calculate_social_velocity(posts)

    # Use NLP score as primary if available, otherwise keyword
    primary_score = nlp_score if nlp_method == "finbert" else keyword_score

    # Velocity bonus: trending stocks get a sentiment boost
    # velocity > 3.0 = +0.05 bonus, velocity > 5.0 = +0.10 bonus
    velocity_bonus = 0.0
    if velocity > 5.0:
        velocity_bonus = 0.10
    elif velocity > 3.0:
        velocity_bonus = 0.05

    final_score = min(1.0, primary_score + velocity_bonus)

    # Calculate Price Sector Divergence
    divergence = "none"
    try:
        from execution.asx_scanner import get_stock_data
        df = get_stock_data(asx_code + ".AX", period="1mo")
        if df is not None and len(df) >= 5:
            p_old = float(df["Close"].iloc[-5])
            p_new = float(df["Close"].iloc[-1])
            ret_5d = ((p_new - p_old) / p_old) * 100
            
            if ret_5d <= -3.0 and final_score >= 0.70:
                divergence = "bullish"
            elif ret_5d >= 5.0 and final_score <= 0.40:
                divergence = "bearish"
    except Exception as e:
        logger.warning(f"Divergence calc failed for {asx_code}: {e}")

    result = {
        "asx_code": asx_code,
        "sentiment_score": round(final_score, 3),
        "sentiment_method": nlp_method,
        "nlp_score": round(nlp_score, 3),
        "keyword_score": round(keyword_score, 3),
        "bullish_signals": bullish,
        "bearish_signals": bearish,
        "post_count": len(posts),
        "social_velocity": velocity,
        "velocity_status": "viral" if velocity > 5.0 else ("trending" if velocity > 3.0 else "normal"),
        "divergence": divergence,
        "analyzed_at": datetime.now().isoformat()
    }

    logger.info(f"Sentiment for {asx_code}: score={final_score:.3f} ({nlp_method}) | "
                f"velocity={velocity:.1f}x ({result['velocity_status']}) | posts={len(posts)}")

    return result


def batch_sentiment(asx_codes):
    """
    Runs sentiment analysis on a list of ASX codes.
    Returns a list of sentiment results sorted by score descending.
    """
    results = []
    for code in asx_codes:
        result = get_ticker_sentiment(code)
        results.append(result)

    results.sort(key=lambda x: x["sentiment_score"], reverse=True)

    # Save to data dir
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(data_dir, exist_ok=True)
    output_path = os.path.join(data_dir, "latest_sentiment.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    logger.info(f"Batch sentiment saved to {output_path}")
    return results


if __name__ == "__main__":
    import sys as _sys

    if len(_sys.argv) > 1:
        codes = _sys.argv[1:]
    else:
        codes = ["BRN", "PLS", "IMU", "ZIP", "DEG"]

    print("=" * 60)
    print("HOTCOPPER SENTIMENT ANALYZER")
    print("=" * 60)

    results = batch_sentiment(codes)

    print(f"\n{'Code':<6} {'Score':>7} {'Bull':>5} {'Bear':>5} {'Posts':>6}")
    print("-" * 35)
    for r in results:
        print(f"{r['asx_code']:<6} {r['sentiment_score']:>6.3f} {r['bullish_signals']:>5} "
              f"{r['bearish_signals']:>5} {r['post_count']:>6}")
