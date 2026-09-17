import os

from ..core import hedged_first

UA = {"User-Agent": "delivery-dispatcher/1.0 (local admin tool)",
      # ключ ограничен по Referer — серверный геокодер шлёт заголовок сам
      "Referer": "https://barak-dispatcher.vercel.app"}
GEO_HEDGE_S = 2.0    # столько ждём провайдера, прежде чем подстраховать следующим
GEO_FINAL_S = 4.0    # максимум ожидания после старта последнего провайдера
YANDEX_KEY = os.environ.get("YANDEX_GEOCODER_KEY", "")


def _hedged(providers, hedge_s=GEO_HEDGE_S, final_wait=GEO_FINAL_S):
    """Каскад геокодера поверх общего hedged_first (живёт в core, чтобы не
    тянуть цикл импортов). Возвращает список (может быть пустым)."""
    return hedged_first(providers, hedge_s=hedge_s, final_wait=final_wait) or []
