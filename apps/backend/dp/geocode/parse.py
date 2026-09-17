import math
import re


def _strip_street_type(s):
    s = re.sub(r"^(улица|вуліца|ул\.|ulitsa|ul\.|проспект|пр-т|переулок|пер\.|бульвар|площадь|пл\.)\s*",
               "", (s or "").strip(), flags=re.I)
    # «Советская улица» -> «Советская» (проспекты/площади не трогаем: тип — часть имени)
    return re.sub(r"\s*(улица|вуліца)$", "", s, flags=re.I).strip()


def _extract_house(q):
    m = re.search(r"(\d+[а-яa-zA-Z]*(?:\s*[/-]\s*\d+)?)\s*$", q.strip())
    return m.group(1).replace(" ", "") if m else ""


def _same_house(qnum, hnum):
    if not qnum or not hnum:
        return False
    a = str(hnum).lower().replace(" ", "")
    b = qnum.lower()
    return a == b or a.split("/")[0] == b.split("/")[0] or a.split("к")[0] == b.split("к")[0]


def _bbox(lat, lng, radius_km):
    """Рамка (viewbox/bbox для Nominatim/Photon) радиусом radius_km вокруг точки."""
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * math.cos(math.radians(lat)) or 1.0)
    return f"{lng - dlng},{lat + dlat},{lng + dlng},{lat - dlat}"


_STREET_TYPES = re.compile(r"(проспект|праспект|площадь|плошча|бульвар|шоссе|тракт|"
                           r"переулок|завулак|набережная|спуск|линия)", re.I)


def _clean_place(p):
    """«Поколюбичский сельский Совет» -> «Поколюбичский»."""
    return re.sub(r"\s*(сельский совет|сельсовет|сельскі савет)$", "", (p or "").strip(), flags=re.I)


def _place_label(street, place, hn="", is_street=True):
    """«Гомель, ул. Тельмана, 19». Микрорайоны (Мельников Луг и пр.) не показываем."""
    name = (street or "").strip()
    if is_street and name and not _STREET_TYPES.search(name):
        name = "ул. " + name
    if hn:
        name = f"{name}, {hn}" if name else str(hn)
    place = _clean_place(place)
    if place and place.lower() != (street or "").strip().lower():
        return f"{place}, {name}" if name else place
    return name or place or "точка"


def _suggest_normalize(label):
    """«1, улица Тельмана, Гомель» -> «Гомель, ул. Тельмана, 1»."""
    parts = [p.strip() for p in label.split(",") if p.strip()]
    if not parts:
        return ""
    if len(parts) >= 2 and not re.match(r"^\d", parts[0]):
        parts = parts[1:] + [parts[0]]
    if len(parts) >= 3 and re.match(r"^\d", parts[1]):
        parts = [parts[0], parts[2], parts[1]]
    return ", ".join(re.sub(r"^улица\s+", "ул. ", p) for p in parts if p)


def _lev(a, b, maxd=2):
    """Левенштейн с отсечкой: >maxd — сразу maxd+1 (без полной матрицы)."""
    la, lb = len(a), len(b)
    if abs(la - lb) > maxd:
        return maxd + 1
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        best = cur[0]
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (a[i - 1] != b[j - 1]))
            if cur[j] < best:
                best = cur[j]
        if best > maxd:
            return maxd + 1
        prev = cur
    return prev[lb]


def _typo_max(word):
    """Допуск опечаток: 1 для слов >=6 букв, 2 для >=9; короткие — строго."""
    n = len(word)
    return 2 if n >= 9 else (1 if n >= 6 else 0)


def _word_like(t, h):
    """Слово запроса t против слова адреса h: точное/префикс или опечатка."""
    if t == h or h.startswith(t) or t.startswith(h):
        return True
    m = _typo_max(t)
    return m > 0 and _lev(t, h, m) <= m


def _tok(s):
    # ё -> е: «еремино» должен находить «Ерёмино»
    return [t.replace("ё", "е") for t in re.split(r"[^а-яёa-z0-9]+", (s or "").lower()) if len(t) >= 3]
