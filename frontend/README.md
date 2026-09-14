# Диспетчер доставки — фронтенд (Next.js)

Next.js (App Router) + React Leaflet. Обращается к Flask API через rewrites-прокси (`/api/*` → `BACKEND_URL`, по умолчанию `http://127.0.0.1:5050`), поэтому куки сессии работают без CORS.

## Запуск

```bash
npm install
npm run build
npm run start          # http://127.0.0.1:3000
```

Бэкенд (Flask, порт 5050) должен быть запущен из корня проекта: `python app.py`.

Переменные окружения:

- `BACKEND_URL` — адрес Flask (по умолчанию `http://127.0.0.1:5050`)

## Структура

- `app/login/page.tsx` — вход (`/api/login`)
- `app/page.tsx` → `components/Console.tsx` — весь интерфейс диспетчера
- `components/MapView.tsx` — карта (Google-тайлы `hl=ru`, линии маршрутов)
- `components/GeoInput.tsx` — поле адреса с подсказками (`/api/geocode`)
- `lib/api.ts` — типы и `api()` (401 → редирект на `/login`)

## Разработка

```bash
npm run dev            # http://127.0.0.1:3000, hot reload
```

Требуется Node 20+.
