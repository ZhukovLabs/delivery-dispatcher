import sys

import requests

from .geo import log


def login(args):
    s = requests.Session()
    r = s.post(args.base + "/api/login",
               json={"email": args.email, "password": args.password}, timeout=15)
    if r.status_code != 200:
        log("login failed: " + str(r.status_code))
        sys.exit(1)
    log("login ok: " + args.email)
    return s


def state(s, base):
    return s.get(base + "/api/state", timeout=15).json()


def point_by_name(st, wanted):
    wl = wanted.strip().lower()
    for p in st.get("points") or []:
        if p["name"].strip().lower() == wl:
            return p
    for p in st.get("points") or []:
        if p["name"].strip().lower().startswith(wl):
            return p
    return None


def ensure_bots(s, args, st):
    """--add: создать недостающих ботов и привязать синтетические чаты."""
    if not args.add:
        return
    serial = 0
    for c in st["couriers"]:
        try:
            if int(c.get("tg_chat_id") or 0) >= args.chat_base:
                serial += 1
        except ValueError:
            pass
    for spec in args.add.split(","):
        spec = spec.strip()
        if not spec or ":" not in spec:
            continue
        pname, _, n = spec.partition(":")
        p = point_by_name(st, pname)
        if not p:
            log(f"точка «{pname}» не найдена — пропуск")
            continue
        for i in range(1, int(n) + 1):
            name = f"{args.prefix} {p['name']} {i}"
            if any(c["name"] == name for c in st["couriers"]):
                serial += 1
                continue
            rr = s.post(args.base + "/api/couriers", json={"name": name}, timeout=15)
            if rr.status_code != 200:
                log(f"создать «{name}»: {rr.status_code} {rr.text[:100]}")
                serial += 1
                continue
            st = state(s, args.base)
            c = next(c for c in st["couriers"] if c["name"] == name)
            s.post(args.base + f"/api/couriers/{c['id']}/point",
                   json={"point_id": p["id"]}, timeout=15)
            serial += 1
            rr = s.post(args.base + f"/api/couriers/{c['id']}/bind",
                        json={"chat_id": args.chat_base + serial,
                              "login": "courier-bot"}, timeout=15)
            log(f"бот «{name}» создан, чат {args.chat_base + serial}, "
                f"точка «{p['name']}»"
                + ("" if rr.status_code == 200 else f" (bind {rr.status_code})"))
    return state(s, args.base)


def home_of(st, pid):
    for p in st.get("points") or []:
        if p["id"] == pid:
            return (p["lat"], p["lng"])
    return None
