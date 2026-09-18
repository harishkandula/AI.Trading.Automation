from datetime import datetime
from zoneinfo import ZoneInfo

from market_ai.models import Candidate

DISCLAIMER = "Research only. Not investment advice. Market investments involve risk and possible capital loss."

def render_report(title: str, candidates: list[Candidate]) -> dict:
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    ranked = sorted(candidates, key=lambda x: x.score, reverse=True)
    return {
        "title": title,
        "generated_at": now.isoformat(),
        "disclaimer": DISCLAIMER,
        "candidates": [c.to_dict() for c in ranked],
    }
