from decimal import Decimal
from unittest import TestCase

from quantlab.domain.decimal_utils import as_decimal
from quantlab.domain.sizing import SizePlan

from tests.support import eth_swap


class InstrumentAndSizingTests(TestCase):
    def test_binary_float_is_rejected_at_domain_boundary(self) -> None:
        with self.assertRaises(TypeError):
            as_decimal(0.1, field="unsafe")  # type: ignore[arg-type]

    def test_margin_leverage_notional_and_quantity_are_distinct(self) -> None:
        plan = SizePlan.from_margin(
            instrument=eth_swap(),
            margin="100",
            leverage="6",
            price="2500",
        )

        self.assertEqual(plan.requested_margin, Decimal("100"))
        self.assertEqual(plan.required_margin, Decimal("100"))
        self.assertEqual(plan.notional, Decimal("600.00"))
        self.assertEqual(plan.base_quantity, Decimal("0.24"))
        self.assertEqual(plan.contract_quantity, Decimal("24"))
        self.assertEqual(plan.base_quantity * plan.reference_price, plan.notional)
        self.assertEqual(plan.required_margin * plan.leverage, plan.notional)

    def test_contract_quantization_never_increases_exposure(self) -> None:
        plan = SizePlan.from_margin(
            instrument=eth_swap(),
            margin="101",
            leverage="6",
            price="2500",
        )

        self.assertEqual(plan.contract_quantity, Decimal("24"))
        self.assertEqual(plan.notional, Decimal("600.00"))
        self.assertEqual(plan.required_margin, Decimal("100.00"))
        self.assertEqual(plan.unused_margin, Decimal("1.00"))

    def test_trade_precision_is_enforced(self) -> None:
        spec = eth_swap()
        with self.assertRaisesRegex(ValueError, "price_tick"):
            spec.validate_trade(price="2500.001", contract_quantity="10")
        with self.assertRaisesRegex(ValueError, "contract_step"):
            spec.validate_trade(price="2500.00", contract_quantity="10.5")

    def test_too_small_size_is_rejected_after_quantization(self) -> None:
        with self.assertRaisesRegex(ValueError, "min_contracts"):
            SizePlan.from_margin(
                instrument=eth_swap(),
                margin="1",
                leverage="1",
                price="2500",
            )
