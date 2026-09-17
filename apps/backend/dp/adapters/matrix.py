"""Рабочая матрица времени: дорожная + светофоры + часовые коэффициенты."""
from .routing import routing_table
from ..state import ROAD_FACTOR
from ..domain.geo import haversine_km

def build_time_matrix(points, settings, k_homes=1):
    """Матрица времени в СЕКУНДАХ.

    points = k_homes точек выдачи + заказы. Время дуги =
    (OSRM-время + светофоры_с/км) × коэффициент пробок + вручение
    (ТОЛЬКО на дугах прибытия в заказ, узлы >= k_homes:
    возврат в свою/чужую точку выдачи вручением не является).
    OSRM отдаёт время свободного потока: без пробок и без остановок на
    регулируемых перекрёстках, поэтому светофоры моделируются отдельной
    надбавкой за километр пути (по умолчанию 15 с/км ≈ светофор каждые
    ~1.2 км и ~18 с ожидания).
    Секунды (а не минуты, как раньше) убирают «пол в 1 минуту» на коротких
    городских дугах — решатель видит честную геометрию близких адресов.
    Возвращает (матрица_сек, дороги_использованы, матрица_расстояний_м|None,
    провайдер).
    """
    handover_s = max(0, int(settings["handover_min"])) * 60
    traffic = max(1.0, float(settings.get("traffic", 1.3)))
    lights = max(0.0, float(settings.get("lights_sec_per_km", 24)))  # с/км
    speed = max(5.0, float(settings["speed_kmh"]))
    n = len(points)
    durations, distances, provider = (routing_table(points) if n >= 2
                                      else (None, None, "offline"))
    m = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if durations is not None:
                km = (distances[i][j] / 1000.0 if distances
                      else haversine_km(points[i], points[j]) * ROAD_FACTOR)
                seconds = durations[i][j] + km * lights
            else:  # запасной вариант: оценка по прямой
                km = haversine_km(points[i], points[j]) * ROAD_FACTOR
                seconds = km / speed * 3600.0 + km * lights
            # traffic (и почасовой коэффициент при пересчёте /base*час)
            # масштабирует дорожное время ЦЕЛИКОМ — светофорные очереди
            # растут вместе с потоком. Раньше lights добавлялись ПОСЛЕ
            # traffic: обратное деление /base_traffic их «сдувало» на
            # 1/traffic (~-23% при 1.3) в плоские часы
            seconds *= traffic
            t = max(1, int(round(seconds)))
            if j >= k_homes:
                t += handover_s
            m[i][j] = t
    return m, durations is not None, distances, provider


# Почасовые коэффициенты дорожной нагрузки (Гомель). Утренний пик резкий:
# с ~7:05 (пригородные потоки Новобелицы/Романовичей, школы), самый плотный
# 7:15-8:40; вечерний 17:00-19:00 (центр, мост, вокзал); обеденный мини-пик.
