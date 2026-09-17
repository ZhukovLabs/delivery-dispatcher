#!/bin/bash
# Ночной бэкап БД диспетчера: консистентный снимок (backup-API sqlite,
# WAL-safe) → gzip → ротация → копия в Telegram (за пределы дома).
# Ставится как systemd-timer dispatcher-backup.timer (см. README).
set -u
APP=/home/zhukovlabs/dispatcher
DEST="$APP/backups"
CHAT=1160368886        # получатель копии (TG_TEST_REDIRECT с ноутбука)
TS=$(date +%F)
mkdir -p "$DEST"
exec 9>/tmp/dispatcher-backup.lock
flock -n 9 || { echo "backup: уже запущен, выходим"; exit 0; }

TMP=$(mktemp /tmp/dispatcher-backup.XXXXXX.db)
trap 'rm -f "$TMP"' EXIT

# --- снимок: официальный backup-API sqlite корректен при WAL-режиме ---
"$APP/venv/bin/python" - "$APP/dispatcher.db" "$TMP" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
    s.backup(d)
PY
[ -s "$TMP" ] || { echo "backup: снимок пуст, прерываем"; exit 1; }

gzip -c "$TMP" > "$DEST/dispatcher-$TS.db.gz"
echo "backup: $DEST/dispatcher-$TS.db.gz ($(du -h "$DEST/dispatcher-$TS.db.gz" | cut -f1))"

# --- ротация: 14 последних ежедневных ---
ls -1t "$DEST"/dispatcher-*.db.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
# --- еженедельная копия по воскресеньям: 8 последних ---
if [ "$(date +%u)" = "7" ]; then
  cp "$DEST/dispatcher-$TS.db.gz" "$DEST/weekly-dispatcher-$TS.db.gz"
  ls -1t "$DEST"/weekly-dispatcher-*.db.gz 2>/dev/null | tail -n +9 | xargs -r rm -f
fi

# --- копия за пределы дома: документ в Telegram ---
TOKEN=$(sed -n 's/^tg_bot_token[[:space:]]*=[[:space:]]*//p' "$APP/config.ini" | head -1)
if [ -n "$TOKEN" ]; then
  if curl -s -m 90 -F "chat_id=$CHAT" \
       -F "caption=backup dispatcher.db $TS" \
       -F "document=@$DEST/dispatcher-$TS.db.gz" \
       "https://api.telegram.org/bot$TOKEN/sendDocument" | grep -q '"ok":true'; then
    echo "backup: копия отправлена в Telegram"
  else
    echo "backup: ОШИБКА отправки в Telegram" >&2
  fi
else
  echo "backup: tg_bot_token не найден в config.ini — копия только локальная" >&2
fi
