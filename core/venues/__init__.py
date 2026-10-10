"""Multi-venue adapter layer (10-10 d): one BaseVenue interface, a Binance wrapper and two PROTOTYPE adapters.

Nothing here is wired into core/engine.py: the engine still trades through core.engine.BinanceVenue / LiveVenue.
The MEXC / Hyperliquid adapters build and sign requests but send nothing without an injected transport (and a
signer for Hyperliquid) - see docs/research_upgrade/report_exchange_pivot_and_zero_fee_2026-10-10.md.
"""
from core.venues.base import (BaseVenue, MinSizeError, PostOnlyRejected, VENUE_SPECS, VenueDisabled,  # noqa: F401
                              VenueError)
