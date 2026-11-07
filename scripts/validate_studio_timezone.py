"""Проверить выбор установщика до записи .env, без доступа к базе и сети."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.studio_calendar import calendar_zone

if len(sys.argv) != 2 or not sys.argv[1].strip():
    raise SystemExit("Choose an IANA studio timezone, for example UTC or America/New_York.")
try:
    calendar_zone(sys.argv[1])
except ValueError as error:
    raise SystemExit(str(error))
print(sys.argv[1])
