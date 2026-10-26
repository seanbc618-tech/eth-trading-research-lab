"""Strict Decimal conversion helpers.

Binary floats are deliberately rejected at domain boundaries. Exchange strings,
integers, and Decimal instances are accepted so monetary calculations remain
reproducible.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TypeAlias


DecimalInput: TypeAlias = Decimal | int | str
ZERO = Decimal("0")


def as_decimal(value: DecimalInput, *, field: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{field} must not be bool or binary float")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (int, str)):
        result = Decimal(value)
    else:
        raise TypeError(f"{field} must be Decimal, int, or str")
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def positive_decimal(value: DecimalInput, *, field: str) -> Decimal:
    result = as_decimal(value, field=field)
    if result <= ZERO:
        raise ValueError(f"{field} must be greater than zero")
    return result
