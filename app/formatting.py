def format_station(st: dict) -> str:
    fuels = st.get("fuels") or "нет данных"
    return (
        f"⛽ <b>{st.get('name') or 'Без названия'}</b>\n"
        f"📍 {st.get('address') or '—'}\n"
        f"🛢 В наличии: {fuels}\n"
        f"🕒 Последняя оплата: {st.get('last_payment_at') or '—'}"
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
