"""WS-хаб: рассылка состояния и лёгких гео-тиков по румам депо."""
import asyncio

from ..core import _geo_payload, _payload, _points_ids
from .hub import sio

async def _broadcast() -> None:
    rooms = sio.manager.rooms.get("/", {})
    for pid in _points_ids():
        room = f"depot:{pid}"
        if not rooms.get(room):
            continue  # в этом депо никого — не собираем payload зря
        payload = await asyncio.to_thread(_payload, None, pid)
        await sio.emit("state", payload, room=room)


async def _broadcast_geo() -> None:
    """Тик движения лёгким событием: позиция/скорость/оценки курьеров."""
    rooms = sio.manager.rooms.get("/", {})
    if not any(rooms.get(f"depot:{pid}") for pid in _points_ids()):
        return
    snap = await asyncio.to_thread(_geo_payload)
    for pid in _points_ids():
        room = f"depot:{pid}"
        if rooms.get(room):
            await sio.emit("geo", snap, room=room)
