"""Pricing calculation. Support has reported incorrect customer totals."""
from decimal import Decimal, ROUND_HALF_UP
from .validation import validate_order


def quote(lines, discount=Decimal("0")):
    validate_order(lines, discount)
    subtotal = sum((Decimal(line["unit_price"]) * Decimal(line["quantity"]).quantize(Decimal("0.01")) for line in lines), Decimal("0"))
    shipping = Decimal("0") if subtotal >= Decimal("100") else Decimal("5")
    total = ((subtotal + shipping) * (1 - discount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"subtotal": subtotal, "shipping": shipping, "total": total}

    def discounted_total(self):
        return self.subtotal * (1 - self.discount)
