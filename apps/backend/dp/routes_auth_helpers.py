"""Хелперы аутентификации (без HTTP-ручек)."""
from .shims import request

def _client_ip():
    """IP клиента для лимита попыток входа.

    Бэкенд стоит за funnel-прокси tailscale: remote_addr там всегда один и
    тот же (прокси), и без X-Forwarded-For все клиенты делят один лимит.
    Заголовку верим ТОЛЬКО от локальных апстримов (loopback / CGNAT
    tailscale 100.64.0.0/10): снаружи к 127.0.0.1 не достучаться, а
    прямые LAN-клиенты подделать XFF не могут.
    """
    import ipaddress
    ra = request.remote_addr or "?"
    try:
        ipobj = ipaddress.ip_address(ra)
        trusted = (ipobj.is_loopback
                   or ipobj.version == 4
                   and ipobj in ipaddress.ip_network("100.64.0.0/10"))
    except ValueError:
        trusted = False
    if trusted:
        xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if xff:
            return xff
    return ra
