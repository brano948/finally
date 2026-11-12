"""Market data subsystem for FinAlly.

Public API:
    PriceUpdate               - Immutable price snapshot dataclass
    PriceCache                - Thread-safe in-memory price store
    MarketDataSource          - Abstract interface for data providers
    SimulatorDataSource       - GBM simulator implementation (default)
    MassiveDataSource         - Massive (Polygon.io) REST poller implementation
    create_market_data_source - Factory that selects simulator or Massive
    create_stream_router      - FastAPI router factory for the SSE endpoint
"""

from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .models import PriceUpdate
from .simulator import SimulatorDataSource
from .stream import create_stream_router

__all__ = [
    "PriceUpdate",
    "PriceCache",
    "MarketDataSource",
    "SimulatorDataSource",
    "MassiveDataSource",
    "create_market_data_source",
    "create_stream_router",
]
