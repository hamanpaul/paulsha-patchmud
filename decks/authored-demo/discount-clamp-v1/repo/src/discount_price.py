def final_price(price, percent_off):
    """折扣後價格：percent_off 應限制在 0..100，超出範圍必須夾住。"""
    return price * (100 - percent_off) / 100
