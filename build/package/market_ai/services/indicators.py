def sma(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError("insufficient history")
    return sum(values[-period:]) / period

def pct_change(values: list[float], period: int) -> float:
    if len(values) <= period or values[-period-1] == 0:
        raise ValueError("insufficient history")
    return values[-1] / values[-period-1] - 1

def normalize(value: float, low: float, high: float) -> float:
    if high <= low:
        raise ValueError("invalid bounds")
    return max(0.0, min(100.0, 100.0 * (value-low)/(high-low)))
