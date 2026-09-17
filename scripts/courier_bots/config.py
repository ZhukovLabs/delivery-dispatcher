import argparse


def parse_args():
    ap = argparse.ArgumentParser(description="Боты-курьеры: эмуляция гео и развозки")
    ap.add_argument("--base", default="http://127.0.0.1:5050")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--add", default="",
                    help='создать ботов: "Точка:N,Точка:M" (idempotent)')
    ap.add_argument("--prefix", default="Бот")
    ap.add_argument("--speed", type=float, default=150.0, help="км/ч")
    ap.add_argument("--dwell", type=float, default=60.0, help="сек у адреса")
    ap.add_argument("--tick", type=float, default=0.5, help="сек между тиками")
    ap.add_argument("--chat-base", type=int, default=9100000)
    ap.add_argument("--osrm", default="http://127.0.0.1:5000")  # локальный OSRM: боты едут ровно по тем же дорогам, что рисует карта
    ap.add_argument("--no-autoclose", action="store_true",
                    help="не подтверждать доставку в диалоге (кнопки жмёт человек)")
    return ap.parse_args()
