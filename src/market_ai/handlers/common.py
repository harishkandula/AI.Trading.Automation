from market_ai.config import Settings
from market_ai.providers.mock import MockBrokerProvider

settings = Settings()
settings.validate_safety()

def provider():
    if settings.broker_provider != "mock":
        raise RuntimeError("Only mock provider is enabled until Groww is configured")
    return MockBrokerProvider()
