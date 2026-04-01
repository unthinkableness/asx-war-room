"""
NLP Sentiment Analyzer (Layer 3 Execution)
============================================
Uses ProsusAI/finbert — a BERT model fine-tuned on financial text — to
classify post titles as positive, negative, or neutral with confidence scores.

Falls back to keyword-based sentiment if the model cannot be loaded
(e.g., no internet on first download, GPU issues, etc).

First run downloads ~420MB of model weights to ~/.cache/huggingface/.
"""

import os
import sys
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.logger import setup_logger

logger = setup_logger("NLPSentiment")

# ---------------------------------------------------------------------------
# Model Loading (lazy, cached)
# ---------------------------------------------------------------------------

_pipeline = None
_model_loaded = False
_load_attempted = False


def _load_model():
    """
    Lazily loads the FinBERT pipeline. Called once on first use.
    Returns True if the model loaded successfully.
    """
    global _pipeline, _model_loaded, _load_attempted

    if _load_attempted:
        return _model_loaded

    _load_attempted = True

    try:
        from transformers import pipeline as hf_pipeline
        logger.info("Loading FinBERT model (ProsusAI/finbert)...")
        logger.info("First run will download ~420MB of model weights.")

        _pipeline = hf_pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            truncation=True,
            max_length=512,
        )
        _model_loaded = True
        logger.info("FinBERT loaded successfully.")
    except Exception as e:
        logger.warning(f"Failed to load FinBERT: {e}")
        logger.warning("Falling back to keyword-based sentiment analysis.")
        _model_loaded = False

    return _model_loaded


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_text(text):
    """
    Classifies a single piece of financial text.

    Args:
        text: A string (e.g., a forum post title).

    Returns:
        {
            "label": "positive" | "negative" | "neutral",
            "score": 0.0 - 1.0  (confidence),
            "method": "finbert" | "keyword"
        }
    """
    if not text or not text.strip():
        return {"label": "neutral", "score": 0.5, "method": "keyword"}

    if _load_model() and _pipeline is not None:
        return _analyze_finbert(text)
    else:
        return _analyze_keywords(text)


def batch_analyze(texts):
    """
    Classifies a list of texts in a single batch (more efficient for FinBERT).

    Args:
        texts: List of strings.

    Returns:
        List of dicts, each with label / score / method.
    """
    if not texts:
        return []

    if _load_model() and _pipeline is not None:
        return _batch_finbert(texts)
    else:
        return [_analyze_keywords(t) for t in texts]


def get_aggregate_score(results):
    """
    Converts a list of per-text results into a single 0.0 - 1.0 sentiment score.

    Positive → score mapped to 0.5 - 1.0
    Negative → score mapped to 0.0 - 0.5
    Neutral  → 0.5
    """
    if not results:
        return 0.5

    total_weight = 0.0
    weighted_sum = 0.0

    for r in results:
        confidence = r.get("score", 0.5)
        label = r.get("label", "neutral").lower()

        if label == "positive":
            value = 0.5 + (confidence * 0.5)  # Maps to 0.5 - 1.0
        elif label == "negative":
            value = 0.5 - (confidence * 0.5)  # Maps to 0.0 - 0.5
        else:
            value = 0.5

        weighted_sum += value * confidence
        total_weight += confidence

    if total_weight == 0:
        return 0.5

    return round(weighted_sum / total_weight, 3)


# ---------------------------------------------------------------------------
# Internal: FinBERT
# ---------------------------------------------------------------------------

def _analyze_finbert(text):
    """Runs a single text through FinBERT."""
    try:
        result = _pipeline(text[:512])[0]
        return {
            "label": result["label"].lower(),
            "score": round(result["score"], 4),
            "method": "finbert"
        }
    except Exception as e:
        logger.warning(f"FinBERT inference failed: {e}")
        return _analyze_keywords(text)


def _batch_finbert(texts):
    """Runs a batch of texts through FinBERT."""
    try:
        # Truncate all texts to 512 chars
        truncated = [t[:512] if t else "" for t in texts]
        raw_results = _pipeline(truncated)
        return [
            {
                "label": r["label"].lower(),
                "score": round(r["score"], 4),
                "method": "finbert"
            }
            for r in raw_results
        ]
    except Exception as e:
        logger.warning(f"FinBERT batch inference failed: {e}")
        return [_analyze_keywords(t) for t in texts]


# ---------------------------------------------------------------------------
# Internal: Keyword Fallback
# ---------------------------------------------------------------------------

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


def _analyze_keywords(text):
    """Basic keyword-based sentiment (the original fallback method)."""
    if not text:
        return {"label": "neutral", "score": 0.5, "method": "keyword"}

    text_lower = text.lower()
    bullish = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
    bearish = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)

    total = bullish + bearish
    if total == 0:
        return {"label": "neutral", "score": 0.5, "method": "keyword"}

    ratio = bullish / total
    if ratio > 0.6:
        label = "positive"
    elif ratio < 0.4:
        label = "negative"
    else:
        label = "neutral"

    return {"label": label, "score": round(ratio, 4), "method": "keyword"}


# ---------------------------------------------------------------------------
# CLI Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_texts = [
        "PLS is going to the moon! Massive breakout incoming 🚀",
        "This stock is a total scam, management dumping shares",
        "Quarterly results released, production numbers steady",
        "Huge drill results — bonanza grade assays confirmed!",
        "Dilution risk is real, another placement coming soon",
    ]

    print("=" * 60)
    print("NLP SENTIMENT ANALYZER TEST")
    print("=" * 60)

    results = batch_analyze(test_texts)
    for text, result in zip(test_texts, results):
        print(f"\n  Text:   {text[:60]}...")
        print(f"  Label:  {result['label']}")
        print(f"  Score:  {result['score']}")
        print(f"  Method: {result['method']}")

    agg = get_aggregate_score(results)
    print(f"\n  Aggregate Score: {agg}")
