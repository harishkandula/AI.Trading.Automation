from dataclasses import dataclass
import os

@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("APP_ENV", "dev")
    bucket: str = os.getenv("DATA_BUCKET", "")
    table: str = os.getenv("STATE_TABLE", "")
    trading_mode: str = os.getenv("TRADING_MODE", "RESEARCH_ONLY")
    live_order_placement: bool = os.getenv("LIVE_ORDER_PLACEMENT", "false").lower() == "true"
    broker_provider: str = os.getenv("BROKER_PROVIDER", "mock")

    def validate_safety(self) -> None:
        if self.live_order_placement or self.trading_mode != "RESEARCH_ONLY":
            raise RuntimeError("This release permits RESEARCH_ONLY mode only")
