"""Estimativa de frequência (execuções/dia) a partir de expressões de agendamento.

Suporta: cron de 5 campos (Cloud Scheduler, Dataform, Airflow), presets do
Airflow (@hourly, @daily...), timedelta serializado do Airflow e o formato
"App Engine cron" usado pelo BigQuery Data Transfer Service
("every 24 hours", "every day 03:00", "every mon,wed 09:00", "every 15 minutes").
Retorna None quando não é possível estimar (não inventa frequência).
"""
from __future__ import annotations

import re

PRESETS = {"@hourly": 24.0, "@daily": 1.0, "@midnight": 1.0, "@weekly": 1 / 7, "@monthly": 12 / 365,
           "@quarterly": 4 / 365, "@yearly": 1 / 365, "@annually": 1 / 365, "@once": 0.0, "@continuous": None}
DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun", "monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday"}


def _field_count(field: str, lo: int, hi: int) -> int | None:
    total = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit():
                return None
            step = int(s)
        if part in ("*", "?"):
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            if not (x.isdigit() and y.isdigit()):
                return None
            a, b = int(x), int(y)
        elif part.isdigit():
            a = int(part)
            b = hi if step > 1 else a
        else:
            return None  # nomes (MON, JAN) tratados à parte
        total.update(range(a, b + 1, step))
    return len(total)


def _dow_count(field: str) -> int | None:
    if field in ("*", "?"):
        return 7
    names = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
    norm = field.lower()
    for i, n in enumerate(names):
        norm = norm.replace(n, str(i))
    c = _field_count(norm.replace("7", "0"), 0, 6)
    return c


def cron_runs_per_day(expr: str) -> float | None:
    parts = expr.split()
    if len(parts) == 6:  # alguns formatos incluem segundos
        parts = parts[1:]
    if len(parts) != 5:
        return None
    minute, hour, dom, month, dow = parts
    m = _field_count(minute, 0, 59)
    h = _field_count(hour, 0, 23)
    if m is None or h is None:
        return None
    per_day = m * h
    if dom not in ("*", "?"):
        d = _field_count(dom, 1, 31)
        if d is None:
            return None
        per_day *= d / 30.44
    if dow not in ("*", "?"):
        w = _dow_count(dow)
        if w is None:
            return None
        per_day *= w / 7
    if month not in ("*", "?"):
        mo = _field_count(month, 1, 12)
        if mo is None:
            return None
        per_day *= mo / 12
    return round(per_day, 4)


def appengine_runs_per_day(expr: str) -> float | None:
    s = expr.strip().lower()
    m = re.match(r"every\s+(\d+)\s+(minutes?|mins?|hours?)\b", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        window = 24.0
        w = re.search(r"from\s+(\d{1,2}):(\d{2})\s+to\s+(\d{1,2}):(\d{2})", s)
        if w:
            window = max((int(w.group(3)) * 60 + int(w.group(4)) - int(w.group(1)) * 60 - int(w.group(2))) / 60, 0)
        return round(window * 60 / n if unit.startswith("min") else window / n, 4)
    if re.match(r"every\s+day\b", s) or re.match(r"every\s+\d{1,2}:\d{2}", s):
        return 1.0
    m = re.match(r"every\s+([a-z,\s]+?)\s+\d{1,2}:\d{2}", s)
    if m:
        days = [d.strip() for d in m.group(1).split(",") if d.strip()]
        if all(d in DAYS for d in days):
            return round(len(days) / 7, 4)
    if re.search(r"of\s+(month|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", s):
        n = len(re.findall(r"\d+(st|nd|rd|th)", s)) or 1
        return round(n * 12 / 365, 4)
    return None


def runs_per_day(schedule) -> float | None:
    """Aceita string (cron/preset/App Engine) ou dict de timedelta do Airflow."""
    if schedule is None:
        return None
    if isinstance(schedule, dict):
        t = schedule.get("__type")
        if t == "TimeDelta":
            secs = schedule.get("days", 0) * 86400 + schedule.get("seconds", 0) + schedule.get("microseconds", 0) / 1e6
            return round(86400 / secs, 4) if secs else None
        if t == "CronExpression":
            return runs_per_day(schedule.get("value"))
        if t == "RelativeDelta":
            return None
        return runs_per_day(schedule.get("value")) if "value" in schedule else None
    s = str(schedule).strip()
    if not s or s.lower() in ("none", "null"):
        return None
    if s.lower() in PRESETS:
        return PRESETS[s.lower()]
    if s.lower().startswith("every") or " of " in s.lower():
        return appengine_runs_per_day(s)
    return cron_runs_per_day(s)


def frequency_label(rpd: float | None) -> str:
    if rpd is None:
        return "desconhecida/sob demanda"
    if rpd == 0:
        return "execução única"
    if rpd >= 96:
        return "contínua/near real-time (<=15 min)"
    if rpd >= 24:
        return "horária ou mais frequente"
    if rpd > 1:
        return "várias vezes ao dia"
    if rpd >= 0.99:
        return "diária"
    if rpd >= 1 / 7 - 1e-6:
        return "semanal"
    return "mensal ou menor"
