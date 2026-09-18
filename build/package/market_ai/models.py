from dataclasses import asdict, dataclass
from datetime import date

@dataclass(frozen=True)
class Bar:
    symbol: str
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int

@dataclass(frozen=True)
class Candidate:
    symbol: str
    score: float
    category: str
    reasons: list[str]
    risks: list[str]
    as_of: str

    def to_dict(self) -> dict:
        return asdict(self)
