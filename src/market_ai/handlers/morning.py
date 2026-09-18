from market_ai.handlers.common import provider
from market_ai.services.scoring import swing_candidate
from market_ai.services.reporting import render_report

def handler(event, context):
    p = provider()
    candidates = [swing_candidate(s, b) for s, b in p.daily_bars().items()]
    return render_report("Morning watchlist", candidates)
