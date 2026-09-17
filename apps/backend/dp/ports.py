# -*- coding: utf-8 -*-
"""Порты (hexagonal): контракты ядра с внешним миром.

Слои dp/ (зависимости идут только внутрь, к домену):

  domain/    чистое ядро: математика, правила планирования — без IO
             (geo, text, model)
  services/  сценарии применения: оркестрация домена над портами
             (solve, solve_geom)
  adapters/  ведомые адаптеры (driven): реализуют порты ниже
             (sqlite_repo, telegram, osrm, ors, routing, geometry,
              matrix, speed, http_hedge)
  dp/*.py    ведущие адаптеры (driving) и презентеры: HTTP-роуты
             (routes_*, geocode), WS-хаб (ws), Telegram-бот (bot_*),
             сборка состояния для клиента (payload); плюс сквозные
             config/state/planstate/users/online/bootstrap.

Порты ниже описаны структурно (Protocol): модули-адаптеры уже
удовлетворяют сигнатурам, DI-контейнер не нужен — точка замены
для тестов/миграции фиксируется типами и картой ниже:

  RouterPort      — матрицы времени и геометрия маршрутов
                    impl: adapters.routing, adapters.geometry,
                          adapters.matrix (+ osrm/ors)
  GeocoderPort    — прямой/обратный геокодинг (каскад провайдеров)
                    impl: dp.geocode
  ChatGatewayPort — отправка сообщений/кнопок Telegram-бота
                    impl: adapters.telegram
  StateRepositoryPort — снимок и восстановление состояния (SQLite)
                    impl: adapters.sqlite_repo, dp.bootstrap.load_state
  TelemetryPort   — скорость курьера по GPS-треку
                    impl: adapters.speed
"""
from typing import Any, Callable, Iterable, Optional, Protocol, Sequence, runtime_checkable


@runtime_checkable
class RouterPort(Protocol):
    """Матрица времени (сек) и геометрия маршрутов между точками."""

    def routing_table(self, coords: Sequence[Sequence[float]]
                      ) -> Optional[tuple]: ...
    def routing_geometry(self, a: Any, b: Any) -> Optional[dict]: ...


@runtime_checkable
class GeocoderPort(Protocol):
    """Геокодинг: адрес <-> координаты (каскад провайдеров)."""

    def geocode(self, q: str, **kw: Any) -> list: ...
    def reverse_geocode(self, lat: float, lng: float) -> Optional[dict]: ...


@runtime_checkable
class ChatGatewayPort(Protocol):
    """Исходящие сообщения бота (chat, текст, клавиатура)."""

    def _tg_send(self, chat_id: int, text: str, **kw: Any) -> Optional[dict]: ...
    def _tg_edit_msg(self, chat_id: int, msg_id: int, text: str,
                     **kw: Any) -> None: ...


@runtime_checkable
class StateRepositoryPort(Protocol):
    """Персистенция состояния: заказы, курьеры, метаданные, история."""

    def _persist_orders(self) -> None: ...
    def _persist_couriers(self) -> None: ...
    def _persist_meta(self, key: str, value: Any) -> None: ...
    def _archive_order(self, o: dict, why: str) -> None: ...


@runtime_checkable
class TelemetryPort(Protocol):
    """Скорость курьера: замер по треку с фолбэком на дефолт."""

    def _courier_speed(self, courier: dict,
                       settings: Optional[dict] = None) -> tuple: ...
