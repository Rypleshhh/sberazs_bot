import json
from datetime import datetime

from app.storage import FUEL_LABELS, FUEL_STATUS_LABELS


def format_payment_time(raw: str | None) -> str:
    """Превращает '2026-07-12T20:17:21+03:00' в '20:17:21 12.07.2026'."""
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    return dt.strftime("%H:%M:%S %d.%m.%Y")


def _parse_fuels(fuels_json: str | None) -> list[dict]:
    if not fuels_json:
        return []
    try:
        return json.loads(fuels_json)
    except (TypeError, ValueError):
        return []


def _format_fuel_line(fuel: dict) -> str:
    ftype = fuel.get("type", "?")
    label = FUEL_LABELS.get(ftype, ftype)
    status = fuel.get("availabilityStatus", "unknown")
    status_label = FUEL_STATUS_LABELS.get(status, status)
    line = f"  • {label}: {status_label}"
    if fuel.get("limitLiters"):
        line += f" (лимит {fuel['limitLiters']} л)"
    return line


TBANK_STATUS_LABELS = {
    "available": "есть",
    "maybe_available": "возможно есть",
    "no_data": "нет данных",
    "not_available": "нет",
}


def _format_tbank_line(tbank_json: str | None) -> str:
    if not tbank_json:
        return ""
    try:
        t = json.loads(tbank_json)
    except (TypeError, ValueError):
        return ""
    if not t:
        return ""

    status = t.get("status", "no_data")
    label = TBANK_STATUS_LABELS.get(status, status)
    confidence = t.get("confidence")
    conf_text = f" ({round(confidence * 100)}%)" if confidence is not None else ""
    return f"\n🏦 T-Bank подтверждает: {label}{conf_text}"


def format_station(st: dict) -> str:
    """Полная карточка станции — для /scan_city и /cities."""
    fuels = _parse_fuels(st.get("fuels_json"))
    fuels_block = "\n".join(_format_fuel_line(f) for f in fuels) if fuels else "  нет данных"
    ops = st.get("operations_count") or 0
    ops_line = f"\n👥 Заправились за последнее время: {ops}" if ops else ""

    return (
        f"⛽ <b>{st.get('name') or 'Без названия'}</b>\n"
        f"📍 {st.get('address') or '—'}\n"
        f"🛢 Топливо:\n{fuels_block}"
        f"{ops_line}\n"
        f"🕒 Последняя оплата: {format_payment_time(st.get('last_payment_at'))}"
        f"{_format_tbank_line(st.get('tbank_json'))}"
    )


def format_resumption(st: dict) -> str:
    """Уведомление о том, что топливо СНОВА появилось после перерыва —
    используется для push-уведомлений подписчикам."""
    resumed = _parse_fuels(st.get("resumed_fuels_json"))
    labels = []
    for f in resumed:
        label = FUEL_LABELS.get(f.get("type"), f.get("type"))
        if f.get("limitLiters"):
            label += f" (лимит {f['limitLiters']} л)"
        labels.append(label)
    fuels_text = ", ".join(labels) if labels else "топливо"

    ops = st.get("operations_count") or 0
    ops_line = f"\n👥 Заправились за последнее время: {ops}" if ops else ""

    return (
        f"⛽ <b>{st.get('name') or 'Без названия'}</b>\n"
        f"📍 {st.get('address') or '—'}\n"
        f"🎉 Снова в наличии: {fuels_text}"
        f"{ops_line}\n"
        f"🕒 Последняя оплата: {format_payment_time(st.get('last_payment_at'))}"
        f"{_format_tbank_line(st.get('tbank_json'))}"
    )


def format_station_list(stations: list[dict], header: str) -> list[str]:
    """
    Возвращает список сообщений (Telegram режет длинные сообщения,
    поэтому группируем станции пачками, а не одним гигантским текстом).
    """
    if not stations:
        return [f"{header}\n\nСвежих данных не найдено."]

    messages = []
    chunk = [header, ""]
    chunk_len = len(header)

    for st in stations:
        block = format_station(st)
        if chunk_len + len(block) > 3500:
            messages.append("\n\n".join(chunk))
            chunk = []
            chunk_len = 0
        chunk.append(block)
        chunk_len += len(block)

    if chunk:
        messages.append("\n\n".join(chunk))

    return messages
