from datetime import date
from market_ai.services.scoring import compounder_candidate
from market_ai.services.reporting import render_report

SYNTHETIC = {
 "DEMOALPHA": {"revenue_cagr": .22, "profit_cagr": .27, "roce": .21, "debt_equity": .25, "cash_conversion": .90},
 "DEMOBETA": {"revenue_cagr": .13, "profit_cagr": .16, "roce": .14, "debt_equity": .80, "cash_conversion": .65},
 "DEMOGAMMA": {"revenue_cagr": .28, "profit_cagr": .31, "roce": .24, "debt_equity": .12, "cash_conversion": 1.05},
}

def handler(event, context):
    items = [compounder_candidate(s, f, date.today().isoformat()) for s, f in SYNTHETIC.items()]
    return render_report("Weekly compounder research", items)
