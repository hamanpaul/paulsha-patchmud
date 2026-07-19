from discount_price import final_price


def test_over_100_clamped():
    assert final_price(100, 150) == 0.0
