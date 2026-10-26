from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantlab.domain.instrument import InstrumentSpec
from quantlab.market_data.candle import Candle


def eth_swap() -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id="ETH-USDT-SWAP",
        base_currency="ETH",
        quote_currency="USDT",
        settlement_currency="USDT",
        contract_size_base=Decimal("0.01"),
        price_tick=Decimal("0.01"),
        contract_step=Decimal("1"),
        min_contracts=Decimal("1"),
        min_notional=Decimal("5"),
    )


def minute_candle(
    index: int,
    *,
    open_price: str = "100",
    close_price: str = "101",
    confirmed: bool = True,
    source_row: int | None = None,
) -> Candle:
    opened = Decimal(open_price)
    closed = Decimal(close_price)
    return Candle(
        instrument_id="BINANCE:ETHUSDT:SPOT",
        interval_seconds=60,
        open_time=datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index),
        open=opened,
        high=max(opened, closed) + Decimal("1"),
        low=min(opened, closed) - Decimal("1"),
        close=closed,
        base_volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        trade_count=10,
        confirmed=confirmed,
        source_row=source_row,
    )
