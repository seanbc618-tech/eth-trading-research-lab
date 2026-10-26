from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase

from quantlab.domain.fill import Fill, FundingPayment
from quantlab.domain.ledger import DuplicateEventConflict, TradingLedger
from quantlab.domain.order import OrderSide
from quantlab.domain.sizing import SizePlan

from tests.support import eth_swap


NOW = datetime(2025, 7, 27, 12, 0, tzinfo=timezone.utc)


def fill(
    fill_id: str,
    side: OrderSide,
    price: str,
    contracts: str,
    *,
    fee_rate: str = "0.0004",
) -> Fill:
    return Fill.from_fee_rate(
        fill_id=fill_id,
        order_id=f"order-{fill_id}",
        instrument=eth_swap(),
        side=side,
        price=price,
        contract_quantity=contracts,
        fee_rate=fee_rate,
        occurred_at=NOW,
    )


class LedgerTests(TestCase):
    def test_legacy_unit_regression_does_not_apply_leverage_twice(self) -> None:
        spec = eth_swap()
        plan = SizePlan.from_margin(
            instrument=spec,
            margin="100",
            leverage="6",
            price="2500",
        )
        ledger = TradingLedger(settlement_currency="USDT", opening_cash="1000")
        opening_fill = Fill.from_fee_rate(
            fill_id="open",
            order_id="order-open",
            instrument=spec,
            side=OrderSide.BUY,
            price="2500",
            contract_quantity=plan.contract_quantity,
            fee_rate="0.0004",
            occurred_at=NOW,
        )
        closing_fill = Fill.from_fee_rate(
            fill_id="close",
            order_id="order-close",
            instrument=spec,
            side=OrderSide.SELL,
            price="2525",
            contract_quantity=plan.contract_quantity,
            fee_rate="0.0004",
            occurred_at=NOW,
        )

        ledger.apply_fill(opening_fill)
        ledger.apply_fill(closing_fill)
        snapshot = ledger.snapshot()

        self.assertEqual(opening_fill.notional, Decimal("600.00"))
        self.assertEqual(opening_fill.fee, Decimal("0.240000"))
        self.assertEqual(closing_fill.fee, Decimal("0.242400"))
        self.assertEqual(snapshot.realized_pnl_gross, Decimal("6.00"))
        self.assertEqual(snapshot.fees, Decimal("0.482400"))
        self.assertEqual(snapshot.net_pnl, Decimal("5.517600"))
        self.assertEqual(snapshot.equity, Decimal("1005.517600"))
        self.assertEqual(snapshot.positions[0].signed_base_quantity, Decimal("0.00"))
        self.assertIsNone(snapshot.positions[0].average_entry_price)

    def test_partial_fills_keep_weighted_cost_and_realized_pnl(self) -> None:
        ledger = TradingLedger(settlement_currency="USDT", opening_cash="1000")
        ledger.apply_fill(fill("buy-1", OrderSide.BUY, "2000", "10", fee_rate="0"))
        ledger.apply_fill(fill("buy-2", OrderSide.BUY, "2200", "10", fee_rate="0"))
        ledger.apply_fill(fill("sell-1", OrderSide.SELL, "2300", "10", fee_rate="0"))

        snapshot = ledger.snapshot({"ETH-USDT-SWAP": Decimal("2300")})

        self.assertEqual(snapshot.positions[0].average_entry_price, Decimal("2100"))
        self.assertEqual(snapshot.positions[0].signed_base_quantity, Decimal("0.10"))
        self.assertEqual(snapshot.realized_pnl_gross, Decimal("20.00"))
        self.assertEqual(snapshot.unrealized_pnl_gross, Decimal("20.00"))
        self.assertEqual(snapshot.net_pnl, Decimal("40.00"))

    def test_long_and_short_pnl_are_symmetric(self) -> None:
        long_ledger = TradingLedger(settlement_currency="USDT", opening_cash="0")
        long_ledger.apply_fill(fill("long-open", OrderSide.BUY, "2500", "24", fee_rate="0"))
        long_ledger.apply_fill(fill("long-close", OrderSide.SELL, "2525", "24", fee_rate="0"))

        short_ledger = TradingLedger(settlement_currency="USDT", opening_cash="0")
        short_ledger.apply_fill(fill("short-open", OrderSide.SELL, "2500", "24", fee_rate="0"))
        short_ledger.apply_fill(fill("short-close", OrderSide.BUY, "2475", "24", fee_rate="0"))

        self.assertEqual(long_ledger.snapshot().net_pnl, Decimal("6.00"))
        self.assertEqual(short_ledger.snapshot().net_pnl, Decimal("6.00"))

    def test_reversal_realizes_old_side_and_opens_new_cost_basis(self) -> None:
        ledger = TradingLedger(settlement_currency="USDT", opening_cash="0")
        ledger.apply_fill(fill("open", OrderSide.BUY, "2000", "10", fee_rate="0"))
        ledger.apply_fill(fill("reverse", OrderSide.SELL, "2100", "15", fee_rate="0"))

        snapshot = ledger.snapshot({"ETH-USDT-SWAP": Decimal("2000")})

        self.assertEqual(snapshot.realized_pnl_gross, Decimal("10.00"))
        self.assertEqual(snapshot.positions[0].signed_base_quantity, Decimal("-0.05"))
        self.assertEqual(snapshot.positions[0].average_entry_price, Decimal("2100"))
        self.assertEqual(snapshot.unrealized_pnl_gross, Decimal("5.00"))

    def test_fill_and_funding_events_are_idempotent(self) -> None:
        ledger = TradingLedger(settlement_currency="USDT", opening_cash="100")
        opening = fill("open", OrderSide.BUY, "2500", "2")
        first_fill = ledger.apply_fill(opening)
        duplicate_fill = ledger.apply_fill(opening)
        funding = FundingPayment(
            event_id="funding-1",
            instrument_id="ETH-USDT-SWAP",
            amount=Decimal("-1.25"),
            currency="USDT",
            occurred_at=NOW,
        )
        first_funding = ledger.apply_funding(funding)
        duplicate_funding = ledger.apply_funding(funding)

        snapshot = ledger.snapshot({"ETH-USDT-SWAP": Decimal("2500")})

        self.assertTrue(first_fill.applied)
        self.assertFalse(duplicate_fill.applied)
        self.assertTrue(first_funding.applied)
        self.assertFalse(duplicate_funding.applied)
        self.assertEqual(snapshot.event_count, 2)
        self.assertEqual(snapshot.funding, Decimal("-1.25"))
        self.assertEqual(snapshot.fees, Decimal("0.020000"))
        self.assertEqual(snapshot.net_pnl, Decimal("-1.270000"))

    def test_duplicate_id_with_different_content_fails_closed(self) -> None:
        ledger = TradingLedger(settlement_currency="USDT", opening_cash="0")
        ledger.apply_fill(fill("same", OrderSide.BUY, "2500", "2"))
        with self.assertRaises(DuplicateEventConflict):
            ledger.apply_fill(fill("same", OrderSide.BUY, "2501", "2"))

    def test_empty_ledger_cannot_invent_close_profit(self) -> None:
        snapshot = TradingLedger(settlement_currency="USDT", opening_cash="1000").snapshot()
        self.assertEqual(snapshot.realized_pnl_gross, Decimal("0"))
        self.assertEqual(snapshot.unrealized_pnl_gross, Decimal("0"))
        self.assertEqual(snapshot.net_pnl, Decimal("0"))
        self.assertEqual(snapshot.equity, Decimal("1000"))
