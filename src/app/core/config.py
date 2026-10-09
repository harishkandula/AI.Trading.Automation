from __future__ import annotations

import os
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    app_env: str = "dev"
    log_level: str = "INFO"

    # Database
    database_url: str = "sqlite:///./local.db"

    # AWS
    aws_region: str = "ap-south-1"
    market_data_bucket: str = ""
    state_table: str = ""

    # Groww
    groww_api_key: str = ""
    groww_api_secret: str = ""
    groww_totp_secret: str = ""
    groww_access_token: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Email
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""

    # Safety – never enable in this release
    enable_live_orders: bool = False

    # Trading / portfolio
    research_capital: float = 5_000_000.0
    risk_per_trade_pct: float = 0.005
    max_open_positions: int = 10
    max_new_positions_per_session: int = 3
    intraday_capital: float = 500_000.0
    intraday_risk_per_trade_pct: float = 0.0025
    intraday_max_positions: int = 3

    # Broker
    broker_provider: str = "mock"
    trading_mode: str = "RESEARCH_ONLY"

    @field_validator("enable_live_orders", mode="before")
    @classmethod
    def _no_live_orders(cls, v: object) -> bool:
        parsed = str(v).lower() in ("1", "true", "yes")
        if parsed:
            raise ValueError("ENABLE_LIVE_ORDERS must remain false in this release")
        return False

    @model_validator(mode="after")
    def _validate_safety(self) -> "Settings":
        if self.trading_mode != "RESEARCH_ONLY":
            raise ValueError("TRADING_MODE must be RESEARCH_ONLY in this release")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "prod"

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_username and self.email_to)

    @property
    def groww_configured(self) -> bool:
        return bool(self.groww_api_key or self.groww_access_token)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
