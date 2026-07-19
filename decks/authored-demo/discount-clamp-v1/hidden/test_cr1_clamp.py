import pytest

from discount_price import final_price


@pytest.mark.parametrize(
    "price,pct,expected",
    [
        (100, 0, 100.0),
        (100, 100, 0.0),
        (100, 150, 0.0),
        (100, -10, 100.0),
        (200, 50, 100.0),
    ],
)
def test_clamp_cases(price, pct, expected):
    assert final_price(price, pct) == expected
