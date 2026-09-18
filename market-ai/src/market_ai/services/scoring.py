from market_ai.models import Bar, Candidate
from market_ai.services.indicators import normalize, pct_change, sma

def swing_candidate(symbol: str, bars: list[Bar]) -> Candidate:
    if len(bars) < 60:
        raise ValueError("At least 60 bars are required")
    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    trend = normalize(closes[-1] / sma(closes, 50) - 1, -0.10, 0.20)
    momentum = normalize(pct_change(closes, 20), -0.15, 0.25)
    volume = normalize(volumes[-1] / sma([float(v) for v in volumes], 20), 0.5, 2.5)
    breakout = 100.0 if closes[-1] >= max(closes[-20:]) else 30.0
    score = round(0.30*trend + 0.30*momentum + 0.20*volume + 0.20*breakout, 2)
    reasons = ["Above 50-day average" if closes[-1] > sma(closes, 50) else "Below 50-day average",
               "20-day high" if breakout == 100 else "No 20-day breakout"]
    risks = [] if score >= 60 else ["Weak composite signal"]
    return Candidate(symbol, score, "SWING_RESEARCH", reasons, risks, bars[-1].day.isoformat())

def compounder_candidate(symbol: str, fundamentals: dict, as_of: str) -> Candidate:
    required = ["revenue_cagr", "profit_cagr", "roce", "debt_equity", "cash_conversion"]
    missing = [k for k in required if k not in fundamentals]
    if missing:
        raise ValueError(f"Missing fundamentals: {missing}")
    growth = (normalize(fundamentals["revenue_cagr"], 0, .30) + normalize(fundamentals["profit_cagr"], 0, .35))/2
    quality = (normalize(fundamentals["roce"], .08, .30) + normalize(fundamentals["cash_conversion"], .4, 1.2))/2
    balance = 100-normalize(fundamentals["debt_equity"], 0, 1.5)
    score = round(.40*growth + .40*quality + .20*balance, 2)
    return Candidate(symbol, score, "COMPOUNDER_RESEARCH", ["Growth, quality and leverage screen"], [], as_of)
