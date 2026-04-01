import os
import sys
import json

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

def generate_ai_explanation(ticker, scan_data=None, sentiment_data=None):
    """
    Generates a human-like 'AI Explanation' of a stock's current momentum setup
    by synthesizing the metrics from the latest scan and sentiment snapshots.
    """
    # Fallback to loading data if not provided
    if not scan_data:
        scan_path = os.path.join(PROJECT_ROOT, "data", "latest_scan.json")
        try:
            with open(scan_path, "r") as f:
                scan_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            scan_data = []

    if not sentiment_data:
        sentiment_path = os.path.join(PROJECT_ROOT, "data", "latest_sentiment.json")
        try:
            with open(sentiment_path, "r") as f:
                sentiment_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            sentiment_data = []

    # Find the ticker in the data
    s_scan = next((s for s in scan_data if s["asx_code"] == ticker), None)
    s_sent = next((s for s in sentiment_data if s["asx_code"] == ticker), None)

    if not s_scan:
        return f"No recent momentum scan data available for {ticker}."

    # Extract metrics
    rvol = s_scan.get("rvol", 0)
    gap_up = s_scan.get("gap_up", False)
    gap_pct = s_scan.get("gap_pct", 0)
    vwap_dist = s_scan.get("vwap_distance_pct", 0)
    score = s_scan.get("score", 0)
    
    # Sentiment metrics
    sent_score = getattr(s_sent, "sentiment_score", 0.5) if s_sent else s_scan.get("sentiment_score", 0.5)
    vel_status = getattr(s_sent, "velocity_status", "normal") if s_sent else s_scan.get("velocity_status", "normal")

    # ─── NLG (Natural Language Generation) Engine ───
    paragraphs = []

    # 1. Headline / Volume
    if rvol >= 5.0:
        paragraphs.append(f"{ticker} is experiencing extreme institutional or retail accumulation today, trading at a massive {rvol:.1f}x its normal average volume.")
    elif rvol >= 2.5:
        paragraphs.append(f"{ticker} has triggered our high-volume scanners, trading at {rvol:.1f}x average volume today, indicating strong active liquidity.")
    else:
        paragraphs.append(f"{ticker}'s volume profile remains relatively muted at {rvol:.1f}x average volume.")

    # 2. Gap & VWAP Price Action
    if gap_up:
        paragraphs.append(f"The asset executed an aggressive 'Gap & Go' maneuver out of the open, jumping +{gap_pct:.1f}%.")
        if vwap_dist > 0:
            paragraphs.append(f"It has successfully defended this gap and is currently trending +{vwap_dist:.1f}% above its daily VWAP, indicating buyers remain in control of the intraday auction.")
        else:
            paragraphs.append(f"However, sellers have stepped in, pushing it {abs(vwap_dist):.1f}% below the VWAP, suggesting the gap is acting as a " + ("bull trap" if rvol > 3 else "liquidity exhaustion event") + ".")
    else:
        if vwap_dist > 5.0:
            paragraphs.append(f"Intraday price action is exceptionally bullish, pushing +{vwap_dist:.1f}% away from the VWAP without relying on a morning gap.")
        elif vwap_dist > 0:
            paragraphs.append(f"It is demonstrating healthy intraday drift, currently holding +{vwap_dist:.1f}% above the VWAP support level.")
        else:
            paragraphs.append(f"Current price action is struggling, trading {abs(vwap_dist):.1f}% below the VWAP on elevated volume.")

    # 3. Sentiment & Retail Context (from FinBERT/HotCopper)
    if vel_status == "viral":
        paragraphs.append(f"Social sentiment algorithms (FinBERT) classify this stock as highly 'Viral'. Retail attention is accelerating rapidly, providing the necessary exogenous catalyst fuel required for outsized momentum runs.")
    elif vel_status == "trending":
        paragraphs.append(f"Discussion velocity on forums is 'Trending', indicating retail traders are actively accumulating positions.")
    else:
        if sent_score > 0.6:
            paragraphs.append(f"While social velocity is normal, the baseline sentiment remains net-positive ({sent_score:.2f}).")
        elif sent_score < 0.4:
            paragraphs.append(f"Retail sentiment appears bearish ({sent_score:.2f}), which could pose a risk if momentum fails.")

    # 4. Final Verdict
    if score > 100:
        paragraphs.append(f"With a composite score of {score:.1f}, this is an A+ tier momentum setup.")
    elif score > 50:
        paragraphs.append(f"Generating a composite score of {score:.1f}, {ticker} represents a solid, statistically significant trade opportunity.")
    else:
        paragraphs.append(f"Its composite score of {score:.1f} is relatively weak. Use caution.")

    return " ".join(paragraphs)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(generate_ai_explanation(sys.argv[1]))
    else:
        print("Usage: python ai_explainer.py TICKER")
