"""New teaching plugin: repeated long/flat targets from a trailing mean.

This generic example is not a private research strategy or an alpha claim.
"""
from decimal import Decimal
from quantlab.market_data.replay import SignalAction, SignalProposal, StrategyContext


class TeachingMeanStrategy:
    def __init__(self, window: int = 4):
        if isinstance(window, bool) or not isinstance(window, int) or window < 2:
            raise ValueError("window must be an integer >= 2")
        self.window = window

    def on_confirmed_candle(self, context: StrategyContext) -> SignalProposal | None:
        if len(context.history) < self.window:
            return None
        mean = sum((bar.close for bar in context.history[-self.window:]), Decimal(0)) / self.window
        action = SignalAction.LONG if context.current.close > mean else SignalAction.EXIT
        return SignalProposal(action, "teaching-close-above-mean" if action is SignalAction.LONG else "teaching-flat-target")
