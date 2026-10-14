import unittest
from orders.pricing import quote
from decimal import Decimal, ROUND_HALF_UP


class TestPricing(unittest.TestCase):
    def test_quote(self):
        lines = [
            {"unit_price": "10.00", "quantity": 2},
            {"unit_price": "5.00", "quantity": 3}
        ]
        discount = Decimal("0.10")
        expected = {
            "subtotal": Decimal("35.00"),
            "shipping": Decimal("5.00"),
            "total": Decimal("40.00")
        }
        result = quote(lines, discount)
        self.assertEqual(result, expected)

    def test_discounted_total(self):
        lines = [
            {"unit_price": "10.00", "quantity": 2},
            {"unit_price": "5.00", "quantity": 3}
        ]
        discount = Decimal("0.10")
        expected = Decimal("35.00") * (1 - discount)
        result = quote(lines, discount)
        self.assertEqual(result["subtotal"], expected)
