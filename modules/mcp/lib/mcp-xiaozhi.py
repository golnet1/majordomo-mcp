#!/usr/bin/env python3
"""
Универсальный MCP-сервер для MajorDoMo + xiaozhi
С поддержкой дублирующихся алиасов (например, "комната отдыха" в освещении и колонках).
Все действия логируются в единый файл /opt/mcp-bridge/logs/actions.log
"""
import sys
import os
import json
import logging
import re
from mcp.server.fastmcp import FastMCP
from datetime import datetime, timedelta

# === Импорт класса MajorDoMoMCP ===
from mcp_class import MajorDoMoMCP, device_type_matches, room_matches, normalize_days, normalize_time, normalize_text, WEEKDAY_ORDER, SPEAKER_TYPES, SPEAKER_DEFAULT_VOLUME, sensor_kind_title, LIB_REVISION

# === Настройка ===
log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper())
logging.basicConfig(stream=sys.stderr, level=log_level, format="%(levelname)s: %(message)s")
logger = logging.getLogger("mcp_majordomo")

# === Создаем экземпляр класса ===
mcp_instance = MajorDoMoMCP()

# === MCP-сервер ===
mcp = FastMCP("Majordomo Universal")


def lib_files_info() -> str:
    """Дата и размер файлов Python-части модуля: по ним видно, какой код запущен."""
    folder = os.path.dirname(os.path.abspath(__file__))
    parts = []
    for name in ("mcp_class.py", "mcp-xiaozhi.py"):
        try:
            stat = os.stat(os.path.join(folder, name))
            stamp = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            parts.append(f"{name}: {stamp}, {stat.st_size} б")
        except OSError:
            parts.append(f"{name}: файл не найден")
    return "; ".join(parts)


logger.info(f"[mcp] xiaozhi: Python-код ревизия {LIB_REVISION}; {lib_files_info()}")

mcp_instance.debmes(f"Starting servers: mcp", "mcp")


# === Уточняющие ответы (правила диалога) ===================================
# Модуль не хранит контекст диалога: уточнение задаёт LLM, а мы возвращаем
# структурированный ответ со списком вариантов.
def clarification_response(res) -> dict:
    """Превращает результат резолвера в ответ-уточнение для LLM."""
    status = res.get("status")
    if status == "need_room":
        rooms = res.get("rooms") or []
        return {
            "needs_room": True,
            "question": "В какой комнате?",
            "rooms": rooms,
            "type": res.get("type"),
            "candidates": res.get("candidates") or [],
            "message": "Уточните комнату: " + ", ".join(rooms),
        }
    if status == "need_device":
        candidates = res.get("candidates") or []
        return {
            "needs_device": True,
            "question": "Какое именно устройство?",
            "room": res.get("room"),
            "type": res.get("type"),
            "candidates": candidates,
            "message": "Уточните устройство: " + ", ".join(c.get("title", "") for c in candidates),
        }
    if status == "not_found":
        available = res.get("available") or []
        return {
            "not_found": True,
            "type": res.get("type"),
            "room": res.get("room"),
            "available": available,
            "message": "Не найдено. Доступные устройства: " + ", ".join(
                c.get("title", "") for c in available
            ),
        }
    return {"error": f"Не удалось определить устройство ({status})"}


def device_titles(result) -> str:
    """«Свет боковой», «Свет нижний» и «Телевизор» — для ответа/озвучки."""
    titles = [d.get("title") for d in (result.get("devices") or []) if d.get("title")]
    return ", ".join(titles)


# Подсказка ассистенту: что делать с командами, накопленными для этой колонки.
# Колонка XiaoZhi сама подключается к модулю, «толкнуть» в неё звук нельзя,
# поэтому отложенные команды отдаются вместе с ответом инструмента, а выполняет
# их ассистент на этой же колонке.
SPEAKER_ACTIONS_HINT = (
    "speaker_actions — команды, накопленные для этой колонки. Выполни их сейчас: "
    "action 'say' — произнеси text вслух дословно; action 'set_volume' — вызови "
    "self.audio_speaker.set_volume со значением value. Затем продолжи обычный ответ."
)


def attach_speaker_actions(result, device_id):
    """Добавляет в ответ инструмента отложенные для этой колонки команды.

    Колонка опознаётся по системному полю device_id (в нём её MAC или ID из
    xiaozhi.me). Если для колонки ничего не накоплено — ответ не меняется.
    """
    if not isinstance(result, dict) or not device_id:
        return result
    try:
        actions = mcp_instance.pop_speaker_actions(device_id)
    except Exception as e:
        logger.warning(f"Не удалось получить команды колонки из очереди: {e}")
        return result
    if not actions:
        return result
    result = dict(result)
    result["speaker_actions"] = actions
    result["speaker_actions_hint"] = SPEAKER_ACTIONS_HINT
    return result


def speaker_command_result(res, ok_message: str) -> dict:
    """Единый ответ инструмента по результату dispatch_speaker_command.

    status='sent'   — команда ушла по прямому каналу (озвучка уже/скоро);
    status='queued' — колонка получит команду при следующем обращении к ней;
    status='error'  — команду выполнить не удалось.
    """
    status = res.get("status")
    if status == "sent":
        return {"success": True, "status": "sent", "channel": res.get("channel"),
                "target": res.get("title"), "room": res.get("room"),
                "message": ok_message}
    if status == "queued":
        return {"success": True, "status": "queued",
                "target": res.get("title"), "room": res.get("room"),
                "message": ok_message,
                "note": ("Колонка получит команду не сразу, а при следующем "
                         "обращении к ней — сообщи это пользователю.")}
    out = {"error": res.get("error", "Не удалось выполнить команду колонке")}
    if "available_rooms" in res:
        out["available_rooms"] = res.get("available_rooms")
    return out


# === СТАРЫЕ МЕТОДЫ (с поддержкой дубликатов и логированием) ===
#@mcp.tool()
def get_property(object: str, property: str) -> dict:
    """Технический метод: получить свойство по object.property"""
    path = f"data/{object}.{property}"
    resp = mcp_instance.call_majordomo("GET", path)
    if resp and resp.status_code == 200:
        try:
            value = resp.json().get("data", resp.text.strip())
        except:
            value = resp.text.strip()
        return {"value": value}
    return {"error": f"Ошибка: {resp.status_code if resp else 'timeout'}"}

#@mcp.tool()
def set_property(object: str, property: str, value: str) -> dict:
    """Технический метод: установить свойство"""
    path = f"data/{object}.{property}"
    payload = {"data": str(value)}
    resp = mcp_instance.call_majordomo("POST", path, data=payload)
    if resp and resp.status_code == 200:
        return {"success": True}
    return {"error": f"MajorDoMo вернул статус {resp.status_code if resp else 'N/A'}"}

#@mcp.tool()
def set_device(device_name: str, state: str) -> dict:
    """Человекочитаемое управление с нормализацией и поддержкой дубликатов. Использует тип 'light'."""
    norm_name = mcp_instance.normalize_query(device_name)
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_name,
        preferred_categories=["light", "devices", "свет", "устройства"],
        required_type="devices.types.light"  # Сначала пробуем этот тип
    )
    # Если не нашли с типом "devices.types.light", пробуем "light"
    if not device_spec:
        aliases = mcp_instance.load_aliases_from_db()
        if norm_name in aliases:
            light_specs = [spec for spec in aliases[norm_name]
                          if spec["type"] == "light" or spec["type"] == "devices.types.light"]
            if light_specs:
                device_spec = light_specs[0]
    
    if not device_spec:
        aliases = mcp_instance.load_aliases_from_db()
        #logger.info(f"Все алиасы: {list(aliases.keys())}")
        available = ", ".join(aliases.keys())
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> set_device: {device_name} [FAILED] Details: {{'error': 'Устройство (реле) не найдено', 'available': '{available}'}}", "mcp")
        return {"error": f"Устройство (реле) '{device_name}' не найдено. Доступные: {available}"}

    value = "1" if state.lower() in ("включи", "включить", "on", "1", "да") else "0"
    result = set_property(device_spec["object"], device_spec["property"], value)
    if "success" in result:
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> set_device: {norm_name} [SUCCESS] Details: {{'state': 'включено' if '{value}' == '1' else 'выключено'}}", "mcp")
    else:
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> set_device: {norm_name} [FAILED] Details: {{'error': '{result.get('error', 'Unknown error')}'}}", "mcp")
    return result

#@mcp.tool()
def get_device(device_name: str) -> dict:
    """Человекочитаемый статус с нормализацией и поддержкой дубликатов. Использует тип 'light'."""
    norm_name = mcp_instance.normalize_query(device_name)
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_name,
        preferred_categories=["light", "devices", "свет", "устройства"],  # Обновлено для БД
        required_type="devices.types.light"  # Только реле
    )
    if not device_spec:
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> get_device: {device_name} [FAILED] Details: {{'error': 'Устройство (реле) не найдено'}}", "mcp")
        return {"error": f"Устройство (реле) '{device_name}' не найдено."}

    result = get_property(device_spec["object"], device_spec["property"])
    if "error" in result:
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> get_device: {norm_name} [FAILED] Details: {{'error': '{result['error']}'}}", "mcp")
        return result
    value_str = str(result["value"])
    status = "включено" if value_str == "1" else "выключено"
    # Логируем через debmes при debug
    if mcp_instance.debug:
        mcp_instance.debmes(f"[mcp] xiaozhi -> get_device: {norm_name} [SUCCESS] Details: {{'status': '{status}', 'raw_value': '{result['value']}'}}", "mcp")
    return {"device": device_name, "status": status, "raw_value": result["value"]}

@mcp.tool()
def list_devices(room: str = "", device_type: str = "", device_id: str = "") -> dict:
    """
    Список устройств: название, комната, тип, синонимы.
    Можно отфильтровать по комнате (room) и/или типу (device_type).
    Тип можно указывать по-русски ('свет', 'колонка', 'датчик') либо служебно
    ('light', 'speaker', 'sensor').
    Название устройства (title) — только для информации: в командах используй
    синонимы (aliases) или типовые слова ('свет', 'шторы').
    """
    devices = mcp_instance.load_devices_from_db()
    # «Колонка» -> audio.speaker и т.п.: переводим типовое слово в тип устройства.
    wanted_type = mcp_instance.extract_device_type(device_type) or device_type
    result = []
    for device in devices:
        if room and not room_matches(device.get("room"), room):
            continue
        if wanted_type and not device_type_matches(device.get("type"), [wanted_type]):
            continue
        result.append({
            "title": device.get("title"),
            "room": device.get("room"),
            "type": device.get("type"),
            "aliases": device.get("aliases"),
        })
    return attach_speaker_actions({"devices": result, "count": len(result)}, device_id)

@mcp.tool()
def list_rooms(device_id: str = "") -> dict:
    """Список комнат из MajorDoMo"""
    resp = mcp_instance.call_majordomo("GET", "rooms")
    if resp and resp.status_code == 200:
        try:
            return attach_speaker_actions({"rooms": resp.json()}, device_id)
        except:
            return {"error": "Invalid JSON response"}
    return {"error": f"MajorDoMo error: {resp.status_code if resp else 'timeout'}"}

@mcp.tool()
def get_room(room_id: str) -> dict:
    """Детали комнаты по ID"""
    resp = mcp_instance.call_majordomo("GET", f"rooms/{room_id}")
    if resp and resp.status_code == 200:
        try:
            return {"room": resp.json()}
        except:
            return {"error": "Invalid JSON response"}
    return {"error": f"MajorDoMo error: {resp.status_code if resp else 'timeout'}"}

@mcp.tool()
def where_am_i(device_id: str = "") -> dict:
    """В какой комнате находится колонка, которая сейчас говорит с пользователем.

    Используй, когда спрашивают: «в какой комнате ты находишься», «где ты»,
    «ты в зале?», «какая это колонка», «в какой комнате эта колонка» и т.п.
    Комната определяется по ID устройства колонки-источника (device_id, системное поле
    XiaoZhi — приходит автоматически, пользователь его не называет) — той же
    логикой, что и команды без комнаты ('включи свет' в комнате колонки).

    Возвращает room — название комнаты. Если комната не определена — error.
    """
    room = mcp_instance.set_request_context(device_id)
    if not room:
        return {
            "error": "Комната колонки не определена",
            "hint": "Проверьте в MajorDoMo устройство-колонку: заполнены ли ID устройства и комната.",
        }
    return attach_speaker_actions({"room": room, "device_id": device_id}, device_id)

@mcp.tool()
def identify_speaker(device_id: str = "") -> dict:
    """Записывает ID этой колонки прямо в поле «ID устройства» в MajorDoMo.

    Используй, когда пользователь говорит: «определи ID», «определи колонку»,
    «какой ID у колонки», «назови свой ID» и т.п. (обычно администратор
    нажимает кнопку «Определить колонку» в форме устройства и ждёт до 10 сек).

    device_id — ID устройства этой колонки (системное поле XiaoZhi, приходит
    автоматически; пользователь его не называет).

    Ответ: ok — ID записан в поле устройства (назови device_id вслух);
    already — колонка уже определена в комнате room, ID не записан (скажи
    об этом вслух); error — режим ожидания не активен/ошибка (скажи
    нажать кнопку «Определить колонку» в MajorDoMo и повторить).
    """
    if not device_id:
        return {
            "error": "ID колонки не передан (device_id пуст)",
            "hint": "Параметр device_id не пришёл — проверьте, что колонка подключена к платформе xiaozhi.me.",
        }
    res = mcp_instance.register_speaker_id(device_id)
    status = res.get("status")
    if status == "ok":
        new_id = res.get("new_id") or 0
        logger.info(f"Определена колонка: device_id='{device_id}' (записано в поле устройства)")
        return {
            "ok": True,
            "device_id": device_id,
            "message": (
                f"ID колонки {device_id} записан в поле ID устройства "
                + (f"#{new_id}." if new_id else ".")
            ),
        }
    if status == "already":
        return {
            "already": True,
            "device_id": device_id,
            "room": res.get("room", ""),
            "title": res.get("title", ""),
            "message": f"Колонка уже определена в комнате: {res.get('room', '?')} — ID не записан.",
        }
    if status == "expired":
        return {
            "error": "Режим ожидания истёк",
            "hint": "Нажмите кнопку «Определить колонку» в MajorDoMo и повторите фразу в течение 10 секунд.",
        }
    if status == "idle":
        return {
            "error": "Кнопка «Определить колонку» не нажата",
            "hint": "Попросите администратора открыть форму колонки и нажать «Определить колонку», затем повторите фразу.",
        }
    return {"error": res.get("message", "Не удалось записать ID колонки")}

# === НОВЫЕ МЕТОДЫ (с TTS, поддержкой дубликатов, логированием и учётом типа) ===
@mcp.tool()
def control_device(device_query: str, action: str, brightness: int = None, room: str = "",
                   device_type: str = "", scope: str = "", tts_feedback: bool = True,
                   device_id: str = "") -> dict:
    """
    Управление устройством (свет, розетка, шторы, телевизор и т.п.) с озвучкой.

    device_query — фраза/псевдоним устройства как её сказал пользователь, например
    'боковой свет', 'свет на кухне', 'телевизор'.
    action — 'включи' или 'выключи'.
    brightness — яркость в процентах (0-100) для диммируемых лампочек, например 30.
      Если не задана, но в device_query есть процент ('свет на 30%'), он будет разобран.
      Если устройство не поддерживает яркость, команда всё равно выполняется (включение
      на полную мощность), а в ответе будет success=true и поле note с пояснением.
      Это НЕ ошибка и переспрашивать пользователя не нужно — просто сообщи результат.
    room — комната, если пользователь её назвал ('Большая комната').
    device_type — тип устройства ('light', 'socket', 'media_device.tv', 'thermostat.ac'...).
    scope — масштаб: 'all_in_room' (весь свет в комнате) или 'all_house' (свет по всему дому).
    device_id — ID устройства колонки, от которой пришла команда (системное поле XiaoZhi,
      приходит автоматически; пользователь его не называет). Если колонка привязана
      к комнате, команда без названия комнаты выполняется в комнате этой колонки.

    Правила уточнения (важно!):
    - если ответ содержит needs_room — спроси у пользователя комнату (список в rooms)
      и повтори вызов с room;
    - если ответ содержит needs_device — спроси, какое именно устройство (список в
      candidates), и повтори вызов с более точным device_query;
    - «весь/все» => scope='all_in_room' (нужна комната); «везде / во всём доме» =>
      scope='all_house'.
    Не используй, если пользователь говорит 'через 1 минуту' или 'в 15:30' (это планировщик).
    """
    logger.info(f"Запрос: '{device_query}' → действие: '{action}', яркость: {brightness}, "
                f"комната: '{room}', тип: '{device_type}', масштаб: '{scope}'")

    source_room = mcp_instance.set_request_context(device_id)

    if brightness is None:
        brightness = mcp_instance.parse_percent(device_query)

    result = mcp_instance.resolve_devices(
        device_query, device_type=device_type or None, room=room or None,
        scope=scope or None, default_room=source_room or None
    )
    logger.info(f"Резолвер: {result.get('status')} ({device_titles(result)})")

    if result.get("status") != "ok":
        return clarification_response(result)

    normalized_action = "off" if str(action).lower().startswith(("выкл", "off", "0", "нет", "потуш", "погас")) else "on"

    applied = []
    errors = []
    warnings = []
    for device in result.get("devices") or []:
        outcome = mcp_instance.set_device_action(device["_spec"], normalized_action, brightness)
        if outcome.get("success"):
            if outcome.get("warning") == "brightness_unsupported":
                # Устройство включено, но яркость не поддерживает: не отдаём
                # наружу технический токен, иначе ассистент «переспрашивает».
                warnings.append(outcome.get("target"))
                outcome = {k: v for k, v in outcome.items() if k != "warning"}
                outcome["brightness_applied"] = False
            applied.append(outcome)
        else:
            errors.append({"target": device.get("title"), "error": outcome.get("error")})

    if not applied:
        error_msg = errors[0]["error"] if errors else "Не удалось выполнить команду"
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> control_device: {device_query} [FAILED] Details: {{'error': '{error_msg}'}}", "mcp")
        if error_msg == "brightness_unsupported":
            return {
                "error": "brightness_unsupported",
                "message": "Это устройство не поддерживает регулировку яркости. "
                           "Можно только включить или выключить.",
                "target": errors[0].get("target") if errors else None,
            }
        return {"error": error_msg, "details": errors}

    state_word = "выключено" if normalized_action == "off" else "включено"
    titles = ", ".join(item.get("target") for item in applied)
    # Яркость считаем выставленной только если её реально удалось применить:
    # для не-диммируемого устройства она отбрасывается (см. warnings).
    brightness_applied = brightness is not None and normalized_action == "on" and not warnings
    feedback = f"{titles}: {state_word}"
    if brightness_applied:
        feedback += f", яркость {brightness}%"
    if warnings:
        feedback += f". Внимание: {', '.join(warnings)} не поддерживает регулировку яркости"
    logger.info(feedback)

    if tts_feedback:
        mcp_instance.say_via_tts(feedback)

    if mcp_instance.debug:
        mcp_instance.debmes(f"[mcp] xiaozhi -> control_device: {device_query} [SUCCESS] Details: {{'state': '{state_word}', 'count': {len(applied)}, 'brightness': '{brightness}'}}", "mcp")

    response = {
        "success": True,
        "state": state_word,
        "targets": [item.get("target") for item in applied],
        "count": len(applied),
        "brightness": brightness if brightness_applied else None,
        "applied": applied,
    }
    if errors:
        response["errors"] = errors
    if warnings:
        # Команда уже выполнена: устройства включены на полную мощность.
        response["brightness_unsupported"] = warnings
        response["note"] = (f"{', '.join(warnings)} не поддерживает регулировку яркости. "
                            "Команда выполнена: устройство включено на полную мощность.")
    return attach_speaker_actions(response, device_id)

@mcp.tool()
def get_device_status(device_query: str, room: str = "", device_type: str = "",
                      scope: str = "", tts_feedback: bool = True, device_id: str = "") -> dict:
    """
    Статус устройства (включено/выключено). Поддерживает комнаты, типы и масштаб.

    Правила уточнения те же, что и у control_device: если в ответе needs_room —
    спроси комнату (rooms) и повтори с room; если needs_device — спроси устройство
    (candidates) и уточни device_query.
    device_id — ID устройства колонки (системное поле XiaoZhi, приходит автоматически);
    если колонка привязана к комнате, статус запрашивается в её комнате.
    """
    logger.info(f"Статус: '{device_query}', комната: '{room}', тип: '{device_type}', масштаб: '{scope}'")

    source_room = mcp_instance.set_request_context(device_id)

    result = mcp_instance.resolve_devices(
        device_query, device_type=device_type or None, room=room or None,
        scope=scope or None, default_room=source_room or None
    )
    if result.get("status") != "ok":
        return clarification_response(result)

    statuses = []
    for device in result.get("devices") or []:
        spec = device["_spec"]
        resp = mcp_instance.call_majordomo("GET", f"data/{spec['object']}.{spec['property']}")
        if resp and resp.status_code == 200:
            try:
                value = resp.json().get("data", resp.text.strip())
            except Exception:
                value = resp.text.strip()
            is_on = str(value) == "1"
            # «Реверс»: устройство с обратной полярностью — отдаём состояние как есть для пользователя.
            if mcp_instance.is_device_reversed(spec):
                is_on = not is_on
            statuses.append({
                "title": device.get("title"),
                "room": device.get("room"),
                "status": "включено" if is_on else "выключено",
                "raw_value": value,
            })
        else:
            statuses.append({
                "title": device.get("title"),
                "room": device.get("room"),
                "error": f"MajorDoMo error: {resp.status_code if resp else 'timeout'}",
            })

    ok_statuses = [item for item in statuses if "status" in item]
    if not ok_statuses:
        return {"error": "Не удалось получить статус", "details": statuses}

    feedback = "; ".join(f"{item['title']}: {item['status']}" for item in ok_statuses)
    if tts_feedback:
        mcp_instance.say_via_tts(feedback)
    if mcp_instance.debug:
        mcp_instance.debmes(f"[mcp] xiaozhi -> get_device_status: {device_query} [SUCCESS] Details: {{'count': {len(ok_statuses)}}}", "mcp")

    return attach_speaker_actions(
        {"success": True, "statuses": statuses, "count": len(ok_statuses)}, device_id
    )

@mcp.tool()
def say_to_speaker(message: str, room: str = "", device: str = "", tts_feedback: bool = False) -> dict:
    """
    Передаёт фразу (объявление) на колонку в указанной комнате.

    Используй, когда пользователь явно просит передать/сказать что-то на
    другую колонку, например: «передай на колонку в кухне: обед готов» или
    «скажи в спальне, что я пришёл». Не вызывай этот инструмент для озвучки
    собственных ответов и подтверждений: обычный ответ пользователю доходит и
    так, без передачи на колонку.
    message — текст, который должна произнести колонка.
    room — комната, где должна прозвучать фраза (например, 'Кухня').
    device — конкретная колонка по псевдониму/синониму (необязательно; иначе ищется в комнате).
    """
    logger.info(f"Передать на колонку: комната='{room}', устройство='{device}', текст='{message}'")

    if not message:
        return {"error": "Не указан текст для передачи"}

    res = mcp_instance.dispatch_speaker_command(
        room=room, device=device, command="say", message=message
    )
    return speaker_command_result(res, f"Фраза передана на колонку: {message}")


@mcp.tool()
def set_speaker_volume(volume: int, room: str = "", device: str = "") -> dict:
    """
    Устанавливает громкость колонки в комнате (0-100).

    Используй, когда просят: «сделай громче колонку в кухне», «убавь звук на
    колонке в спальне», «поставь громкость 30 в комнате отдыха».
    volume — громкость в процентах (0-100).
    room — комната, где стоит колонка (например, 'Кухня').
    device — конкретная колонка по псевдониму/синониму (необязательно).
    """
    try:
        vol = int(volume)
    except (TypeError, ValueError):
        return {"error": "Громкость должна быть числом от 0 до 100"}
    vol = max(0, min(100, vol))
    logger.info(f"Громкость колонки: комната='{room}', устройство='{device}', значение={vol}")

    res = mcp_instance.dispatch_speaker_command(
        room=room, device=device, command="volume", value=vol
    )
    return speaker_command_result(res, f"Громкость {vol}% установлена")


@mcp.tool()
def set_speaker_power(on: bool = True, room: str = "", device: str = "") -> dict:
    """
    Включает или выключает звук колонки в комнате.

    У колонок XiaoZhi нет отдельного «питания»: выключение — это громкость 0,
    включение — возврат к обычной громкости. Используй, когда просят:
    «выключи колонку в спальне», «включи колонку в кухне».
    on — True включить звук, False выключить (громкость 0).
    room — комната, где стоит колонка. device — конкретная колонка (необязательно).
    """
    vol = SPEAKER_DEFAULT_VOLUME if on else 0
    logger.info(f"Питание колонки: комната='{room}', устройство='{device}', on={on} (громкость {vol})")

    res = mcp_instance.dispatch_speaker_command(
        room=room, device=device, command="volume", value=vol
    )
    return speaker_command_result(res, "Колонка включена" if on else "Колонка выключена")

@mcp.tool()
def get_sensor_value(sensor_query: str = "", unit: str = "", tts_feedback: bool = True,
                     device_id: str = "", sensor_type: str = "") -> dict:
    """
    Получает текущее значение сенсора (температура, влажность, давление и т.д.) с возможностью озвучивания через TTS.
    Автоматически используется при вопросах вроде "какая температура в комнате отдыха?", "сколько градусов в бане?", "влажность в зале?" и т.п.
    sensor_type: какую величину вернуть — "температура", "влажность", "давление", "освещённость" и т.п. Указывайте её всегда, когда нужна не температура.
    sensor_query: комната или название датчика (например, "баня", "в комнате отдыха").
    unit: единицы измерения для озвучивания (например, "градусов", "процентов", "Паскаль").
    device_id — ID устройства колонки (системное поле XiaoZhi, приходит автоматически); если
    колонка привязана к комнате, предпочтение отдаётся сенсорам этой комнаты.
    """
    norm_query = mcp_instance.normalize_query(sensor_query)
    kind = mcp_instance.detect_sensor_kind(sensor_type, sensor_query, unit)
    kind_title = sensor_kind_title(kind)
    logger.info(
        f"Запрос сенсора: '{sensor_query}' (тип: '{sensor_type}', ед.: '{unit}') "
        f"→ нормализовано: '{norm_query}', вид: {kind or '-'}"
    )

    source_room = mcp_instance.set_request_context(device_id)

    # Комната, названная прямо в вопросе («какая температура в спальне»). Если
    # она известна, датчик нужной величины ищем в ней, а не только в комнате
    # колонки: иначе при нескольких датчиках дома выбор неоднозначен.
    query_room = mcp_instance.extract_room(sensor_query)

    # Загружаем все алиасы
    aliases = mcp_instance.load_aliases_from_db()
    #logger.info(f"Все алиасы: {list(aliases.keys())}")

    def available_sensors():
        """Список псевдонимов датчиков (подсказка ассистенту)."""
        names = []
        for alias_name, specs in aliases.items():
            for spec in specs:
                if device_type_matches(spec.get("type"), ["sensor"]):
                    names.append(alias_name)
                    break
        return ", ".join(sorted(set(names)))

    # Ищем ТОЛЬКО сенсоры (с учётом типа sensor/devices.types.sensor)
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_query, required_type=["sensor"], room=query_room or source_room or None
    )

    # Если в запросе названа величина (влажность/температура/давление), а найденный
    # датчик её не измеряет — берём датчик нужного вида. Пример: «влажность в бане»
    # ассистент передаёт как sensor_query="баня", unit="процентов"; «баня» — это
    # датчик температуры, а вернуть нужно влажность (датчик в той же комнате).
    if kind and not mcp_instance.spec_has_sensor_kind(device_spec, kind):
        alias_room = (device_spec or {}).get("room")
        device_spec = mcp_instance.find_sensor_by_kind(
            kind,
            rooms=[query_room, (device_spec or {}).get("room"), source_room],
            text=sensor_query,
        )
        if not device_spec:
            available = available_sensors()
            logger.info(f"Датчик «{kind_title}» не найден. Доступные: {available}")
            logger.info(
                f"Диагностика датчиков [{kind}]: комната из вопроса={query_room!r}, "
                f"устройство по алиасу={alias_room!r}, комната колонки={source_room!r}. "
                + " | ".join(mcp_instance.sensor_candidates_report(kind))
            )
            if mcp_instance.debug:
                mcp_instance.debmes(f"[mcp] xiaozhi -> get_sensor_value: {sensor_query} [{kind}] [FAILED] Details: {{'error': 'Датчик не найден', 'available': '{available}'}}", "mcp")
            return {"error": f"Датчик «{kind_title}» не найден", "available": available}

    if not device_spec:
        # Формируем список ТОЛЬКО сенсоров
        available = available_sensors()
        logger.info(f"Сенсор не найден. Доступные: {available}")
        if kind:
            logger.info(
                f"Диагностика датчиков [{kind}]: комната из вопроса={query_room!r}, "
                f"комната колонки={source_room!r}. "
                + " | ".join(mcp_instance.sensor_candidates_report(kind))
            )
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> get_sensor_value: {sensor_query} [FAILED] Details: {{'error': 'Сенсор не найден', 'available': '{available}'}}", "mcp")
        return {"error": f"Сенсор не найден: '{sensor_query}'. Доступные: {available}"}

    logger.info(
        f"Датчик найден: «{device_spec.get('title')}» [{device_spec.get('room') or 'без комнаты'}] "
        f"{device_spec.get('object')}.{device_spec.get('property')}"
    )

    resp = mcp_instance.call_majordomo("GET", f"data/{device_spec['object']}.{device_spec['property']}")
    if resp and resp.status_code == 200:
        try:
            value = resp.json().get("data", resp.text.strip())
        except:
            value = resp.text.strip()
        # Что назвать в озвучке: «баня» / «влажность» / комната датчика.
        label = norm_query or kind_title or (device_spec.get("room") or device_spec.get("title") or "")
        if tts_feedback:
            tts_success = mcp_instance.say_via_tts(f"В {label} {value} {unit}")
            logger.info(f"В {label} {value} {unit}")
            if not tts_success:
                logger.warning(f"TTS не удалось выполнить для {label}")
            else:
                logger.info(f"TTS успешно выполнено")
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> get_sensor_value: {norm_query} [SUCCESS] Details: {{'value': '{value}', 'unit': '{unit}', 'sensor_query': '{sensor_query}', 'kind': '{kind or '-'}'}}", "mcp")
        return attach_speaker_actions(
            {"sensor": norm_query or kind_title, "quantity": kind_title,
             "room": device_spec.get("room"), "value": value, "unit": unit},
            device_id,
        )
    else:
        error_msg = f"MajorDoMo error: {resp.status_code if resp else 'timeout'}"
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> get_sensor_value: {norm_query} [FAILED] Details: {{'error': '{error_msg}', 'sensor_query': '{sensor_query}'}}", "mcp")
        return {"error": error_msg}

#@mcp.tool()
def set_device_parameter(device_query: str, parameter: str, value: str, tts_feedback: bool = True) -> dict:
    """
    Установка параметра устройства (тип 'device') с TTS и поддержкой дубликаций.
    Используется, например, для установки температуры.
    """
    norm_query = mcp_instance.normalize_query(device_query)
    logger.info(f"Запрос установки параметра: '{device_query}' → нормализовано: '{norm_query}', параметр: '{parameter}', значение: '{value}'")

    # Ищем устройство с настраиваемым параметром (термостаты, чайник и т.п.)
    parameter_types = ["thermostat", "thermostat.ac", "cooking.kettle"]
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_query,
        required_type=parameter_types
    )
    if not device_spec:
        # Формируем список ТОЛЬКО устройств с параметрами
        aliases = mcp_instance.load_aliases_from_db()
        relevant_aliases = []
        for alias_name, specs in aliases.items():
            for spec in specs:
                if device_type_matches(spec.get("type"), parameter_types):
                    relevant_aliases.append(alias_name)
                    break
        available = ", ".join(sorted(set(relevant_aliases)))
        logger.info(f"Устройство с параметрами не найдено. Доступные: {available}")
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> set_device_parameter: {device_query} [FAILED] Details: {{'error': 'Устройство с параметрами не найдено', 'available': '{available}'}}", "mcp")
        return {"error": f"Устройство с параметрами не найдено: '{device_query}'. Доступные: {available}"}

    # Выполнение команды
    resp = mcp_instance.call_majordomo("POST", f"data/{device_spec['object']}.{device_spec['property']}", data={"data": str(value)})
    if resp and resp.status_code == 200:
        if tts_feedback:
            mcp_instance.say_via_tts(f"Параметр {parameter} в {norm_query} установлен на {value}")
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> set_device_parameter: {norm_query} [SUCCESS] Details: {{'parameter': '{parameter}', 'value': '{value}', 'device_query': '{device_query}'}}", "mcp")
        return {"success": True, "target": norm_query, "parameter": parameter, "value": value}
    else:
        error_msg = f"MajorDoMo error: {resp.status_code if resp else 'timeout'}"
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> set_device_parameter: {norm_query} [FAILED] Details: {{'error': '{error_msg}', 'device_query': '{device_query}'}}", "mcp")
        return {"error": error_msg}


@mcp.tool()
def run_script(script_name: str, tts_feedback: bool = True) -> dict:
    """Запуск сценария"""
    resp = mcp_instance.call_majordomo("GET", f"script/{script_name}")
    if resp and resp.status_code == 200:
        if tts_feedback:
            mcp_instance.say_via_tts(f"Сценарий {script_name} запущен")
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> run_script: {script_name} [SUCCESS]", "mcp")
        return {"success": True, "script": script_name}
    else:
        error_msg = f"Сценарий '{script_name}' не запущен"
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> run_script: {script_name} [FAILED] Details: {{'error': '{error_msg}'}}", "mcp")
        return {"error": error_msg}

# === ГОЛОСОВОЕ УПРАВЛЕНИЕ ПЛАНИРОВЩИКОМ ===

RU_DAY_NAMES = {"mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт",
                "fri": "Пт", "sat": "Сб", "sun": "Вс"}


def _describe_days(days) -> str:
    """Человекочитаемое описание дней задания."""
    if not days or days == ["once"]:
        return "одноразово"
    if list(days) == list(WEEKDAY_ORDER):
        return "каждый день"
    if list(days) == list(WEEKDAY_ORDER[:5]):
        return "по будням"
    if list(days) == list(WEEKDAY_ORDER[5:]):
        return "по выходным"
    return "по дням: " + ", ".join(RU_DAY_NAMES.get(day, day) for day in days)


def _is_on_action(action) -> bool:
    return str(action or "").strip().lower() in ("включи", "включить", "вкл", "on", "1", "да", "true")


def _device_action_data(device, action):
    """ACTION_DATA {type, linked_object, linked_property} для задания.

    Возвращает (данные, спецификация) или (None, None), если устройство не
    найдено. Фразу разбирает общий резолвер resolve_devices: типовые слова
    («свет», «освещение») задают тип в любом порядке, а остаток ищется по
    псевдониму/синониму. Если резолвер вернул неоднозначность — мягкий
    фолбэк по псевдониму, как в остальных инструментах.
    """
    device_spec = None
    result = mcp_instance.resolve_devices(device)
    if result.get("status") == "ok":
        devices = result.get("devices") or []
        if len(devices) == 1:
            device_spec = devices[0]
    if device_spec is None:
        device_spec = mcp_instance.find_device_by_category_and_type(
            mcp_instance.normalize_query(device)
        )
    if not device_spec:
        return None, None
    return {
        "type": "on" if _is_on_action(action) else "off",
        "linked_object": device_spec.get("object"),
        "linked_property": device_spec.get("property"),
    }, device_spec


@mcp.tool()
def add_scheduler_task(time_str: str, device: str, action: str, repeat_days: list = None) -> dict:
    """
    Добавляет задание в планировщик.
    time_str: "HH:MM" (например, "17:15")
    device: псевдоним (синоним) устройства (например, "улица")
    action: "включи" или "выключи"
    repeat_days: ["mon", "tue", ...], "daily"/"weekdays"/"weekends",
                 русские названия ("пн", "будни") или None (одноразовое)
    """
    normalized_time = normalize_time(time_str)
    if not normalized_time:
        return {"success": False, "error": "bad_time",
                "message": f"Не удалось разобрать время «{time_str}». Укажите в формате ЧЧ:ММ."}

    action_data_dict, device_spec = _device_action_data(device, action)
    if not device_spec:
        return {"success": False, "error": "device_not_found",
                "message": f"Устройство «{device}» не найдено."}
    # В планировщике устройство идентифицируется по TITLE: так его ищет админка
    # (выпадающий список) и исполнение задания. Раньше сюда попадал алиас,
    # из-за чего задание сохранялось, но устройство в форме не подставлялось.
    device_title = device_spec.get("title") or device

    days = normalize_days(repeat_days) if repeat_days is not None else ["once"]

    schedule_date = None
    if days == ["once"]:
        hours, minutes = (int(part) for part in normalized_time.split(":"))
        now = datetime.now()
        target = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        schedule_date = target.strftime("%Y-%m-%d")

    task_id = f"voice_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{device_title.replace(' ', '_')}"
    new_task = {
        "id": task_id,
        "enabled": True,
        "description": f"Голосовое задание: {action} {device_title}",
        "time": normalized_time,
        "days": days,
        "schedule_date": schedule_date,
        "action": {
            "type": "device",
            "device": device_title,
            "state": action,
            "data": action_data_dict,
        },
    }

    schedule = mcp_instance.load_schedule_from_db()
    schedule.append(new_task)
    if not mcp_instance.save_schedule_to_db(schedule):
        return {"success": False, "error": "save_failed",
                "message": "Не удалось сохранить задание в базе данных."}

    if mcp_instance.debug:
        mcp_instance.debmes(
            f"[mcp] xiaozhi -> add_scheduler_task: {task_id} [SUCCESS] "
            f"Details: {{'time': '{normalized_time}', 'device': '{device_title}', "
            f"'action': '{action}', 'days': '{days}', 'date': '{schedule_date}'}}", "mcp")

    return {"success": True, "task_id": task_id,
            "message": f"Задание добавлено: {action} {device_title} в {normalized_time} ({_describe_days(days)})"}


@mcp.tool()
def delete_scheduler_task(task_id: str) -> dict:
    """
    Удаляет задание из планировщика по ID.
    """
    schedule = mcp_instance.load_schedule_from_db()
    original_count = len(schedule)
    schedule = [task for task in schedule if task["id"] != task_id]
    if len(schedule) == original_count:
        return {"success": False, "message": f"Задание с ID '{task_id}' не найдено"}

    if not mcp_instance.save_schedule_to_db(schedule):
        return {"success": False, "message": "Не удалось удалить задание"}

    if mcp_instance.debug:
        mcp_instance.debmes(f"[mcp] xiaozhi -> delete_scheduler_task: {task_id} [SUCCESS]", "mcp")
    return {"success": True, "message": f"Задание '{task_id}' удалено"}


@mcp.tool()
def delete_all_scheduler_tasks() -> dict:
    """
    Удаляет ВСЕ задания из планировщика.
    """
    schedule = mcp_instance.load_schedule_from_db()
    original_count = len(schedule)
    if original_count == 0:
        return {"success": True, "message": "Нет заданий для удаления"}

    if not mcp_instance.save_schedule_to_db([]):
        return {"success": False, "message": "Не удалось удалить задания"}

    if mcp_instance.debug:
        mcp_instance.debmes(
            f"[mcp] xiaozhi -> delete_all_scheduler_tasks: all [SUCCESS] "
            f"Details: {{'deleted_count': '{original_count}'}}", "mcp")
    return {"success": True, "message": f"Все задания ({original_count}) удалены"}


@mcp.tool()
def list_scheduler_tasks() -> dict:
    """
    Возвращает список всех заданий планировщика.
    """
    schedule = mcp_instance.load_schedule_from_db()
    if not schedule:
        message = "Заданий нет."
    else:
        lines = []
        for task in schedule:
            mark = "" if task.get("enabled") else " [выключено]"
            time_str = task.get("time") or "??:??"
            when = _describe_days(task.get("days"))
            desc = task.get("description") or f"{task['action'].get('state')} {task['action'].get('device')}"
            lines.append(f"{time_str} ({when}){mark} — {desc} [id: {task['id']}]")
        message = "Задания: " + "; ".join(lines)

    if mcp_instance.debug:
        mcp_instance.debmes(
            f"[mcp] xiaozhi -> list_scheduler_tasks: all [SUCCESS] "
            f"Details: {{'count': '{len(schedule)}'}}", "mcp")
    return {"tasks": schedule, "message": message}


@mcp.tool()
def add_temporary_scheduler_task(minutes_from_now: int, device: str, action: str) -> dict:
    """
    Добавляет задание, которое выполнится через N минут.
    minutes_from_now: int
    device: псевдоним (синоним) устройства
    action: "включи" или "выключи"
    """
    try:
        minutes = max(1, int(minutes_from_now))
    except (TypeError, ValueError):
        return {"success": False, "error": "bad_minutes",
                "message": "Не удалось разобрать количество минут."}

    target = datetime.now() + timedelta(minutes=minutes)
    # Округляем ВВЕРХ до следующей минуты, чтобы цикл MajorDoMo успел сработать.
    if target.second > 0 or target.microsecond > 0:
        target = target.replace(second=0, microsecond=0) + timedelta(minutes=1)
    time_str = target.strftime("%H:%M")
    schedule_date = target.strftime("%Y-%m-%d")

    action_data_dict, device_spec = _device_action_data(device, action)
    if not device_spec:
        return {"success": False, "error": "device_not_found",
                "message": f"Устройство «{device}» не найдено."}
    # См. add_scheduler_task: храним TITLE устройства, а не произнесённый алиас.
    device_title = device_spec.get("title") or device

    task_id = f"voice_temp_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{device_title.replace(' ', '_')}"
    new_task = {
        "id": task_id,
        "enabled": True,
        "description": f"Временное задание: {action} {device_title} через {minutes} мин",
        "time": time_str,
        "days": ["once"],
        "schedule_date": schedule_date,
        "action": {
            "type": "device",
            "device": device_title,
            "state": action,
            "data": action_data_dict,
        },
    }

    schedule = mcp_instance.load_schedule_from_db()
    schedule.append(new_task)
    if not mcp_instance.save_schedule_to_db(schedule):
        return {"success": False, "error": "save_failed",
                "message": "Не удалось сохранить задание в базе данных."}

    if mcp_instance.debug:
        mcp_instance.debmes(
            f"[mcp] xiaozhi -> add_temporary_scheduler_task: {task_id} [SUCCESS] "
            f"Details: {{'time': '{time_str}', 'date': '{schedule_date}', 'device': '{device_title}', "
            f"'action': '{action}', 'minutes_delay': '{minutes}'}}", "mcp")
    return {"success": True, "task_id": task_id,
            "message": f"Задание добавлено: {action} {device_title} через {minutes} мин (в {time_str})"}

@mcp.tool()
def control_openable(device_query: str, action: str, percent: int = None, tts_feedback: bool = True,
                     device_id: str = "") -> dict:
    """
    Управление устройствами типа openable (ворота, шторы, жалюзи).
    action: 'открой', 'закрой', 'приоткрой', 'останови', 'продолжи'
    percent: для штор, жалюзи, ворот, дверей (0-100)
    device_id — ID устройства колонки (системное поле XiaoZhi, приходит автоматически); если
    колонка привязана к комнате, предпочтение отдаётся устройствам этой комнаты.
    """
    norm_query = mcp_instance.normalize_query(device_query)
    logger.info(f"Запрос управления openable: '{device_query}' → нормализовано: '{norm_query}', действие: '{action}', процент: {percent}")

    source_room = mcp_instance.set_request_context(device_id)

    # Ищем устройство типа openable
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_query,
        required_type=["openable", "openable.curtain", "devices.types.openable", "devices.types.openable.curtain"],
        room=source_room or None
    )
    
    if not device_spec:
        # Формируем список доступных openable устройств
        aliases = mcp_instance.load_aliases_from_db()
        relevant_aliases = []
        for alias_name, specs in aliases.items():
            for spec in specs:
                if device_type_matches(spec.get("type"), ["openable", "openable.curtain"]):
                    relevant_aliases.append(alias_name)
                    logger.info(f"Алиас: {alias_name}")
                    break
        available = ", ".join(sorted(set(relevant_aliases)))
        logger.info(f"Не найдено openable устройство. Доступные: {available}")
        return {"error": f"Не найдено openable устройство: '{device_query}'. Доступные: {available}"}

    # Используем информацию из TRAITS для определения связанных объектов и свойств
    traits = device_spec.get("traits", {})
    logger.info(f"Алиас: {traits}")
    # Определяем объекты и свойства
    on_linked_object = None
    on_linked_property = None
    open_linked_object = None
    open_linked_property = None
    pause_linked_object = None
    pause_linked_property = None
    
    if "on" in traits:
        on_linked_object = traits["on"]["linked_object"]
        on_linked_property = traits["on"]["linked_property"]
    if "open" in traits:
        open_linked_object = traits["open"]["linked_object"]
        open_linked_property = traits["open"]["linked_property"]
    if "pause" in traits:
        pause_linked_object = traits["pause"]["linked_object"]
        pause_linked_property = traits["pause"]["linked_property"]

    # Инициализируем переменные для значений
    value_on = None
    value_open = None
    object_name = None
    property_name = None

    # Обработка действий
    if action.lower() in ["останови", "остановить", "стоп", "пауза"]:
        # Для остановки используем pause trait
        if pause_linked_object and pause_linked_property:
            value_on = "Stop"  # или другое значение для остановки
            object_name = pause_linked_object
            property_name = pause_linked_property
            on_linked_object = None
            on_linked_property = None
        else:
            # Нет трейта паузы — останавливать нечем, не притворяемся успехом
            return {"error": f"Устройство '{device_query}' не поддерживает остановку (нет трейта pause)"}
        # Сохраняем последнюю команду перед остановкой
        last_command = getattr(mcp_instance, 'openable_states', {}).get(norm_query, {}).get('last_command', 'открой')
        if not hasattr(mcp_instance, 'openable_states'):
            mcp_instance.openable_states = {}
        mcp_instance.openable_states[norm_query] = {
            'last_command': last_command,
            'last_percent': mcp_instance.openable_states.get(norm_query, {}).get('last_percent', None),
            'is_paused': True
        }
    elif action.lower() in ["продолжи", "продолжить"]:
        pause_linked_object = None
        pause_linked_property = None
        # Восстанавливаем последнее состояние
        if not hasattr(mcp_instance, 'openable_states'):
            mcp_instance.openable_states = {}
        state = mcp_instance.openable_states.get(norm_query, {})
        if state.get('is_paused'):
            last_cmd = state.get('last_command', 'открой')
            last_pct = state.get('last_percent', None)
            
            # Выполняем последнюю команду заново
            if last_cmd in ["открой", "открыть", "открыта"]:
                if last_pct is not None and 0 <= last_pct <= 100:
                    value_open = str(last_pct)
                    object_name = open_linked_object
                    property_name = open_linked_property
                else:
                    value_on = "1"
                    value_open = "100"
                    object_name = on_linked_object
                    property_name = on_linked_property
            elif last_cmd in ["закрой", "закрыть", "закрыта"]:
                if last_pct is not None and 0 <= last_pct <= 100:
                    value_open = str(100 - last_pct)
                    object_name = open_linked_object
                    property_name = open_linked_property
                else:
                    value_on = "0"
                    value_open = "0"
                    object_name = on_linked_object
                    property_name = on_linked_property
            elif last_cmd in ["приоткрой", "приоткрыть"]:
                value_open = "50" if last_pct is None else str(last_pct)
                object_name = open_linked_object
                property_name = open_linked_property
            else:
                value_on = "Continue"
                object_name = pause_linked_object
                property_name = pause_linked_property
                return {"error": "Неизвестная последняя команда для продолжения"}
            
            # Сбрасываем флаг паузы
            mcp_instance.openable_states[norm_query]['is_paused'] = False
        else:
            return {"error": "Нет сохраненного состояния для продолжения"}
    else:
        # Обработка обычных команд
        pause_linked_object = None
        pause_linked_property = None
        if action.lower() in ["открой", "открыть", "открыта"]:
            if percent is not None and 0 <= percent <= 100:
                # Устанавливаем конкретный процент открытия
                value_open = str(percent)
                value_on = "1" if percent > 0 else "0"
                object_name = open_linked_object
                property_name = open_linked_property
                logger.info(f"Открываю на процент: {object_name}.{property_name} = {value_open}")
            else:
                # Полностью открываем
                value_on = "1"
                value_open = "100"
                object_name = on_linked_object
                property_name = on_linked_property
                logger.info(f"Полностью открываю: {object_name}.{property_name} = {value_open}")
        elif action.lower() in ["закрой", "закрыть", "закрыта"]:
            if percent is not None and 0 <= percent <= 100:
                # Устанавливаем процент закрытия (открыто 100-percent)
                value_open = str(100 - percent)
                value_on = "1" if (100 - percent) > 0 else "0"
                object_name = open_linked_object
                property_name = open_linked_property
                logger.info(f"Закрываю на процент: {object_name}.{property_name} = {value_open}")
            else:
                # Полностью закрываем
                value_on = "0"
                value_open = "0"
                object_name = on_linked_object
                property_name = on_linked_property
                logger.info(f"Полностью закрываю: {object_name}.{property_name} = {value_open}")
        elif action.lower() in ["приоткрой", "приоткрыть"]:
            if percent is not None and 0 <= percent <= 100:
                # Устанавливаем конкретный процент открытия
                value_open = str(percent)
                value_on = "1" if percent > 0 else "0"
                object_name = open_linked_object
                property_name = open_linked_property
                logger.info(f"Приоткрываю на процент: {object_name}.{property_name} = {value_open}")
            else:
                # Устанавливаем среднее значение
                value_open = "50"
                value_on = "1"
                object_name = open_linked_object
                property_name = open_linked_property
                logger.info(f"Приоткрываю: {object_name}.{property_name} = {value_open}")
        else:
            return {"error": f"Неизвестное действие: '{action}'. Используйте 'открой', 'закрой', 'приоткрой', 'останови' или 'продолжи'."}
        
        # Сохраняем команду для возможного продолжения
        if not hasattr(mcp_instance, 'openable_states'):
            mcp_instance.openable_states = {}
        mcp_instance.openable_states[norm_query] = {
            'last_command': action.lower(),
            'last_percent': percent,
            'is_paused': False
        }

    # Проверяем, что объекты и свойства определены
    results = []

    # «Реверс»: для устройств с обратной полярностью инвертируем технические
    # значения (вкл<->выкл, открыть<->закрыть). В ответе и озвучке оставляем
    # логические значения — как их назвал пользователь.
    reversed_device = mcp_instance.is_device_reversed(device_spec)
    if reversed_device:
        send_value_on = "1" if value_on == "0" else ("0" if value_on == "1" else value_on)
        if value_open is not None and str(value_open).lstrip('-').isdigit():
            send_value_open = str(100 - int(value_open))
        else:
            send_value_open = value_open
    else:
        send_value_on = value_on
        send_value_open = value_open

    # Антидребезг: если ровно ту же команду этому устройству только что
    # отправляли — не отправляем повторно (защита от зацикливания ассистента,
    # когда одна и та же команда уходит десятки раз подряд).
    _sends = []
    if value_on is not None and pause_linked_object and pause_linked_property:
        _sends.append((str(pause_linked_object), str(pause_linked_property), str(value_on)))
    if value_on is not None and on_linked_object and on_linked_property:
        _sends.append((str(on_linked_object), str(on_linked_property), str(send_value_on)))
    if value_open is not None and open_linked_object and open_linked_property:
        _sends.append((str(open_linked_object), str(open_linked_property), str(send_value_open)))
    if _sends:
        _device_key = open_linked_object or on_linked_object or pause_linked_object or norm_query
        if mcp_instance.command_recently_sent(_device_key, tuple(_sends)):
            logger.info(f"Антидребезг: повторная команда {_sends} для '{_device_key}' пропущена")
            return attach_speaker_actions(
                {"success": True, "target": norm_query, "command": "set_position",
                 "value_on": value_on, "value_open": value_open, "duplicate": True},
                device_id,
            )

    # Отправляем команду в pause свойство, если значение задано
    if value_on is not None and pause_linked_object and pause_linked_property:
        logger.info(f"Отправляю в pause: {pause_linked_object}.{pause_linked_property} = {value_on}")
        resp_pause = mcp_instance.call_majordomo("POST", f"data/{pause_linked_object}.{pause_linked_property}", data={"data": value_on})
        if resp_pause and resp_pause.status_code != 200:
            results.append(f"MajorDoMo error (on): {resp_pause.status_code if resp_pause else 'timeout'}")

    # Отправляем команду в on свойство, если значение задано
    if value_on is not None and on_linked_object and on_linked_property:
        logger.info(f"Отправляю в on: {on_linked_object}.{on_linked_property} = {send_value_on}")
        resp_on = mcp_instance.call_majordomo("POST", f"data/{on_linked_object}.{on_linked_property}", data={"data": send_value_on})
        if resp_on and resp_on.status_code != 200:
            results.append(f"MajorDoMo error (on): {resp_on.status_code if resp_on else 'timeout'}")
    
    # Отправляем команду в open свойство, если значение задано
    if value_open is not None and open_linked_object and open_linked_property:
        logger.info(f"Отправляю в open: {open_linked_object}.{open_linked_property} = {send_value_open}")
        resp_open = mcp_instance.call_majordomo("POST", f"data/{open_linked_object}.{open_linked_property}", data={"data": send_value_open})
        if resp_open and resp_open.status_code != 200:
            results.append(f"MajorDoMo error (open): {resp_open.status_code if resp_open else 'timeout'}")

    # Проверяем, были ли ошибки
    if results:
        error_msg = "; ".join(results)
        return {"error": error_msg}
    else:
        if tts_feedback:
            if action.lower() in ["останови", "остановить", "стоп"]:
                mcp_instance.say_via_tts(f"{device_query.capitalize()} остановлены")
            else:
                if percent is not None:
                    mcp_instance.say_via_tts(f"{device_query.capitalize()} установлены на {value_open} процентов")
                else:
                    if action.lower() in ["открой", "открыть", "открыта"]:
                        if value_open == "100":
                            action_word = "полностью открыты"
                        else:
                            action_word = f"на {value_open}% открыты"
                    elif action.lower() in ["закрой", "закрыть", "закрыта"]:
                        if value_open == "0":
                            action_word = "полностью закрыты"
                        else:
                            action_word = f"на {value_open}% открыты"
                    else:
                        action_word = f"на {value_open}% открыты"
                    mcp_instance.say_via_tts(f"{device_query.capitalize()} {action_word}")
        return attach_speaker_actions(
            {"success": True, "target": norm_query, "command": "set_position",
             "value_on": value_on, "value_open": value_open},
            device_id,
        )

@mcp.tool()
def get_openable_status(device_query: str, tts_feedback: bool = True, device_id: str = "") -> dict:
    """
    Получение статуса openable устройства (ворота, шторы).
    device_id — ID устройства колонки (системное поле XiaoZhi, приходит автоматически); если
    колонка привязана к комнате, предпочтение отдаётся устройствам этой комнаты.
    """
    norm_query = mcp_instance.normalize_query(device_query)
    logger.info(f"Запрос статуса openable: '{device_query}' → нормализовано: '{norm_query}'")

    source_room = mcp_instance.set_request_context(device_id)

    # Ищем устройство типа openable
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_query,
        preferred_categories=["openable", "openable.curtain"],
        room=source_room or None
    )
    
    if not device_spec:
        # Если не нашли по категории, ищем по типу
        aliases = mcp_instance.load_aliases_from_db()
        if norm_query in aliases:
            openable_specs = [spec for spec in aliases[norm_query]
                             if device_type_matches(spec.get("type"), ["openable", "openable.curtain"])]
            if openable_specs:
                device_spec = openable_specs[0]
    
    if not device_spec:
        # Формируем список доступных openable устройств
        aliases = mcp_instance.load_aliases_from_db()
        relevant_aliases = []
        for alias_name, specs in aliases.items():
            for spec in specs:
                if device_type_matches(spec.get("type"), ["openable", "openable.curtain"]):
                    relevant_aliases.append(alias_name)
                    break
        available = ", ".join(sorted(set(relevant_aliases)))
        logger.info(f"Не найдено openable устройство. Доступные: {available}")
        return {"error": f"Не найдено openable устройство: '{device_query}'. Доступные: {available}"}

    # Используем информацию из TRAITS для определения связанных объектов и свойств
    traits = device_spec.get("traits", {})
    
    # Определяем объекты и свойства
    on_linked_object = None
    on_linked_property = None
    open_linked_object = None
    open_linked_property = None
    
    if "on" in traits:
        on_linked_object = traits["on"]["linked_object"]
        on_linked_property = traits["on"]["linked_property"]
    if "open" in traits:
        open_linked_object = traits["open"]["linked_object"]
        open_linked_property = traits["open"]["linked_property"]

    reversed_device = mcp_instance.is_device_reversed(device_spec)

    # Сначала пробуем получить статус из свойства level (для процентного открытия)
    if open_linked_object and open_linked_property:
        resp = mcp_instance.call_majordomo("GET", f"data/{open_linked_object}.{open_linked_property}")
        if resp and resp.status_code == 200:
            try:
                value = resp.json().get("data", resp.text.strip())
            except:
                value = resp.text.strip()
            
            # Определяем состояние
            try:
                numeric_value = int(value)
                # «Реверс»: у устройства обратная полярность процента открытия.
                if reversed_device:
                    numeric_value = 100 - numeric_value
                if numeric_value == 100:
                    status = "полностью открыто"
                elif numeric_value == 0:
                    status = "полностью закрыто"
                else:
                    status = f"на {numeric_value}% открыто"
            except ValueError:
                status = f"в состоянии {value}"

            if tts_feedback:
                mcp_instance.say_via_tts(f"{device_query.capitalize()} {status}")
            return attach_speaker_actions(
                {"device": norm_query, "status": status, "raw_value": value}, device_id
            )
    
    # Если не удалось получить из level, пробуем из levelWork (on trait)
    if on_linked_object and on_linked_property:
        resp = mcp_instance.call_majordomo("GET", f"data/{on_linked_object}.{on_linked_property}")
        if resp and resp.status_code == 200:
            try:
                value = resp.json().get("data", resp.text.strip())
            except:
                value = resp.text.strip()
            
            # Определяем состояние
            if value == "1":
                is_open = True
            elif value == "0":
                is_open = False
            else:
                is_open = None
            if is_open is None:
                status = f"в состоянии {value}"
            else:
                # «Реверс»: у устройства обратная полярность вкл/выкл.
                if reversed_device:
                    is_open = not is_open
                status = "открыто" if is_open else "закрыто"

            if tts_feedback:
                mcp_instance.say_via_tts(f"{device_query.capitalize()} {status}")
            return attach_speaker_actions(
                {"device": norm_query, "status": status, "raw_value": value}, device_id
            )
    
    error_msg = f"MajorDoMo error: не удалось получить статус устройства"
    return {"error": error_msg}

@mcp.tool()
def control_thermostat(device_query: str, action: str, temperature: float = None, mode: str = None, tts_feedback: bool = True, device_id: str = "") -> dict:
    """
    Управление термостатами (отопление, баня, теплый пол, батарея).
    action: 'включи', 'выключи'
    temperature: для установки температуры (если указана)
    mode: 'thermostat', 'keep_warm' для специальных режимов
    device_id — ID устройства колонки (системное поле XiaoZhi, приходит автоматически); если
    колонка привязана к комнате, предпочтение отдаётся устройствам этой комнаты.
    """
    norm_query = mcp_instance.normalize_query(device_query)
    logger.info(f"Запрос управления термостатом: '{device_query}' → нормализовано: '{norm_query}', действие: '{action}', температура: {temperature}, режим: {mode}")

    source_room = mcp_instance.set_request_context(device_id)

    # Ищем устройство типа thermostat
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_query,
        required_type=["thermostat"],
        room=source_room or None
    )
    logger.info(f"device_spec: {device_spec}")
    if not device_spec:
        # Формируем список доступных термостатов
        aliases = mcp_instance.load_aliases_from_db()
        logger.info(f"Алиасы: {aliases}")
        relevant_aliases = []
        for alias_name, specs in aliases.items():
            for spec in specs:
                if device_type_matches(spec.get("type"), ["thermostat"]):
                    relevant_aliases.append(alias_name)
                    break
        available = ", ".join(sorted(set(relevant_aliases)))
        logger.info(f"Не найдено термостат устройство. Доступные: {available}")
        return {"error": f"Не найдено термостат устройство: '{device_query}'. Доступные: {available}"}

    # Используем информацию из CONFIG для определения параметров
    config = device_spec.get("config", {})
    capabilities = config.get("capabilities", [])
    # Параметры для range capability (температура)
    min_temp = None
    max_temp = None
    precision = None
    
    for cap in capabilities:
        if cap.get("type") == "devices.capabilities.range" and cap.get("parameters", {}).get("instance") == "temperature":
            range_params = cap.get("parameters", {}).get("range", {})
            logger.info(f"Доступные параметры range: {range_params}")
            min_temp = range_params.get("min", 1)
            max_temp = range_params.get("max", 100)
            precision = range_params.get("precision", 1)
            logger.info(f"max: {max_temp}")
            break

    # Проверим, что температура в допустимом диапазоне
    # (если range-возможность не задана, min/max остаются None — пропускаем проверку)
    if temperature is not None and min_temp is not None and max_temp is not None:
        if temperature < min_temp or temperature > max_temp:
            error_msg = f"Температура {temperature}°C вне допустимого диапазона {min_temp}-{max_temp}°C"
            if tts_feedback:
                mcp_instance.say_via_tts(f"{device_query.capitalize()} {error_msg}")
            return {"error": error_msg}

    # Используем информацию из TRAITS для определения связанных объектов и свойств
    traits = device_spec.get("traits", {})
    
    # Определяем объекты и свойства
    on_linked_object = None
    on_linked_property = None
    temp_linked_object = None
    temp_linked_property = None
    keep_warm_linked_object = None
    keep_warm_linked_property = None
    
    if "on" in traits:
        on_linked_object = traits["on"]["linked_object"]
        on_linked_property = traits["on"]["linked_property"]
    if "temperature" in traits:
        temp_linked_object = traits["temperature"]["linked_object"]
        temp_linked_property = traits["temperature"]["linked_property"]
    if "keep_warm" in traits:
        keep_warm_linked_object = traits["keep_warm"]["linked_object"]
        keep_warm_linked_property = traits["keep_warm"]["linked_property"]

    # Обработка действий
    responses = []
    
    if action.lower() in ["включи", "включить"]:
        # Включаем основное устройство (on)
        if on_linked_object and on_linked_property:
            resp_on = mcp_instance.call_majordomo("POST", f"data/{on_linked_object}.{on_linked_property}", data={"data": "1"})
            if resp_on and resp_on.status_code == 200:
                responses.append("устройство включено")
            else:
                error_msg = f"MajorDoMo error (on): {resp_on.status_code if resp_on else 'timeout'}"
                return {"error": error_msg}
        
        # Если указана температура, устанавливаем её
        if temperature is not None:
            if temp_linked_object and temp_linked_property:
                resp_temp = mcp_instance.call_majordomo("POST", f"data/{temp_linked_object}.{temp_linked_property}", data={"data": str(temperature)})
                if resp_temp and resp_temp.status_code == 200:
                    responses.append(f"температура установлена на {temperature}°C")
                else:
                    error_msg = f"MajorDoMo error (temperature): {resp_temp.status_code if resp_temp else 'timeout'}"
                    return {"error": error_msg}
            else:
                return {"error": "Термостат не поддерживает установку температуры"}
        
        # Если указан режим поддержания тепла
        if mode == "keep_warm":
            if keep_warm_linked_object and keep_warm_linked_property:
                resp_keep_warm = mcp_instance.call_majordomo("POST", f"data/{keep_warm_linked_object}.{keep_warm_linked_property}", data={"data": "1"})
                if resp_keep_warm and resp_keep_warm.status_code == 200:
                    responses.append("режим поддержания тепла включён")
                else:
                    error_msg = f"MajorDoMo error (keep_warm): {resp_keep_warm.status_code if resp_keep_warm else 'timeout'}"
                    return {"error": error_msg}
            else:
                return {"error": "Термостат не поддерживает режим поддержания тепла"}
    
    elif action.lower() in ["выключи", "выключить"]:
        # Выключаем основное устройство (on)
        if on_linked_object and on_linked_property:
            resp_on = mcp_instance.call_majordomo("POST", f"data/{on_linked_object}.{on_linked_property}", data={"data": "0"})
            if resp_on and resp_on.status_code == 200:
                responses.append("устройство выключено")
            else:
                error_msg = f"MajorDoMo error (on): {resp_on.status_code if resp_on else 'timeout'}"
                return {"error": error_msg}
        else:
            return {"error": "Термостат не поддерживает включение/выключение"}
    
    else:
        return {"error": f"Неизвестное действие: '{action}'. Используйте 'включи' или 'выключи'."}

    if tts_feedback:
        response_text = f"{device_query.capitalize()} {' и '.join(responses)}"
        mcp_instance.say_via_tts(response_text)
    
    return attach_speaker_actions(
        {"success": True, "target": norm_query, "actions": responses}, device_id
    )

@mcp.tool()
def get_thermostat_status(device_query: str, tts_feedback: bool = True, device_id: str = "") -> dict:
    """
    Получение статуса термостата.
    device_id — ID устройства колонки (системное поле XiaoZhi, приходит автоматически); если
    колонка привязана к комнате, предпочтение отдаётся устройствам этой комнаты.
    """
    norm_query = mcp_instance.normalize_query(device_query)
    logger.info(f"Запрос статуса термостата: '{device_query}' → нормализовано: '{norm_query}'")

    source_room = mcp_instance.set_request_context(device_id)

    # Ищем устройство типа thermostat
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_query,
        required_type=["thermostat", "devices.types.thermostat"],
        room=source_room or None
    )
    
    if not device_spec:
        # Формируем список доступных термостатов
        aliases = mcp_instance.load_aliases_from_db()
        relevant_aliases = []
        for alias_name, specs in aliases.items():
            for spec in specs:
                if device_type_matches(spec.get("type"), ["thermostat"]):
                    relevant_aliases.append(alias_name)
                    break
        available = ", ".join(sorted(set(relevant_aliases)))
        logger.info(f"Не найдено термостат устройство. Доступные: {available}")
        return {"error": f"Не найдено термостат устройство: '{device_query}'. Доступные: {available}"}

    # Используем информацию из TRAITS для определения связанных объектов и свойств
    traits = device_spec.get("traits", {})
    
    # Определяем объекты и свойства
    on_linked_object = None
    on_linked_property = None
    temp_linked_object = None
    temp_linked_property = None
    keep_warm_linked_object = None
    keep_warm_linked_property = None
    
    if "on" in traits:
        on_linked_object = traits["on"]["linked_object"]
        on_linked_property = traits["on"]["linked_property"]
    if "temperature" in traits:
        temp_linked_object = traits["temperature"]["linked_object"]
        temp_linked_property = traits["temperature"]["linked_property"]
    if "keep_warm" in traits:
        keep_warm_linked_object = traits["keep_warm"]["linked_object"]
        keep_warm_linked_property = traits["keep_warm"]["linked_property"]

    status_info = []
    
    # Получаем статус включения
    if on_linked_object and on_linked_property:
        resp_on = mcp_instance.call_majordomo("GET", f"data/{on_linked_object}.{on_linked_property}")
        if resp_on and resp_on.status_code == 200:
            try:
                on_value = resp_on.json().get("data", resp_on.text.strip())
            except:
                on_value = resp_on.text.strip()
            
            if on_value == "1":
                status_info.append("включено")
            else:
                status_info.append("выключено")
        else:
            error_msg = f"MajorDoMo error (on): {resp_on.status_code if resp_on else 'timeout'}"
            return {"error": error_msg}
    
    # Получаем температуру
    if temp_linked_object and temp_linked_property:
        resp_temp = mcp_instance.call_majordomo("GET", f"data/{temp_linked_object}.{temp_linked_property}")
        if resp_temp and resp_temp.status_code == 200:
            try:
                temp_value = resp_temp.json().get("data", resp_temp.text.strip())
            except:
                temp_value = resp_temp.text.strip()
            
            status_info.append(f"температура {temp_value}°C")
        else:
            error_msg = f"MajorDoMo error (temperature): {resp_temp.status_code if resp_temp else 'timeout'}"
            return {"error": error_msg}
    
    # Получаем статус режима поддержания тепла
    if keep_warm_linked_object and keep_warm_linked_property:
        resp_keep_warm = mcp_instance.call_majordomo("GET", f"data/{keep_warm_linked_object}.{keep_warm_linked_property}")
        if resp_keep_warm and resp_keep_warm.status_code == 200:
            try:
                keep_warm_value = resp_keep_warm.json().get("data", resp_keep_warm.text.strip())
            except:
                keep_warm_value = resp_keep_warm.text.strip()
            
            if keep_warm_value == "1":
                status_info.append("режим поддержания тепла включён")
            else:
                status_info.append("режим поддержания тепла выключен")
        else:
            error_msg = f"MajorDoMo error (keep_warm): {resp_keep_warm.status_code if resp_keep_warm else 'timeout'}"
            return {"error": error_msg}

    status_text = f"{device_query.capitalize()} {' и '.join(status_info)}"
    
    if tts_feedback:
        mcp_instance.say_via_tts(status_text)
    
    return attach_speaker_actions(
        {"device": norm_query, "status": status_info}, device_id
    )


# === СЦЕНЫ И ГРУППЫ ========================================================
@mcp.tool()
def list_scenes(tts_feedback: bool = False) -> dict:
    """Список сцен/групп: название, синонимы, включена ли, состав действий."""
    scenes = mcp_instance.load_scenes_from_db()
    items = [
        {
            "title": scene.get("title"),
            "aliases": scene.get("aliases"),
            "enabled": scene.get("enabled"),
            "actions": len(scene.get("actions") or []),
            "detail": _describe_scene(scene),
        }
        for scene in scenes
    ]

    if not items:
        message = "Сцен пока нет."
    else:
        lines = []
        for item in items:
            mark = "" if item["enabled"] else " [выключена]"
            lines.append(f"{item['title']}{mark}: {item['detail']}")
        message = "Сцены: " + "; ".join(lines)

    if tts_feedback:
        mcp_instance.say_via_tts(message)
    return {"scenes": items, "message": message}


@mcp.tool()
def run_scene(scene_name: str, tts_feedback: bool = True, device_id: str = "") -> dict:
    """
    Запускает сцену/группу: «включи вечерний свет», «запусти сцену кино».
    Сцена — это набор заранее настроенных действий (устройство + вкл/выкл + яркость).
    Если в ответе needs_scene — уточни у пользователя название из candidates.
    """
    logger.info(f"Запуск сцены: '{scene_name}'")

    scene, ambiguous = mcp_instance.find_scene(scene_name)
    if not scene:
        available = [s.get("title") for s in mcp_instance.load_scenes_from_db() if s.get("enabled")]
        if ambiguous:
            return {
                "needs_scene": True,
                "question": "Какую сцену запустить?",
                "candidates": available,
                "message": "Уточните сцену: " + ", ".join(available),
            }
        return {
            "error": f"Сцена не найдена: '{scene_name}'",
            "available": available,
            "message": "Доступные сцены: " + ", ".join(available),
        }

    if not scene.get("enabled"):
        return {"error": f"Сцена '{scene.get('title')}' отключена"}

    results = []
    for action in scene.get("actions") or []:
        spec = action.get("device")
        if not spec:
            results.append({"success": False, "device_id": action.get("device_id"),
                            "error": "device_not_found"})
            continue
        results.append(mcp_instance.set_device_action(spec, action.get("action"),
                                                      action.get("brightness")))

    applied = [item for item in results if item.get("success")]
    success = bool(results) and len(applied) == len(results)
    feedback = f"Сцена {scene.get('title')} выполнена" if success \
        else f"Сцена {scene.get('title')}: выполнено {len(applied)} из {len(results)}"
    logger.info(feedback)
    if tts_feedback:
        mcp_instance.say_via_tts(feedback)

    return attach_speaker_actions({
        "success": success,
        "scene": scene.get("title"),
        "applied": len(applied),
        "total": len(results),
        "results": results,
    }, device_id)


# --- Голосовое управление сценами (создание/правка/удаление) ---------------

def _as_list(value):
    """Приводит значение к списку строк.

    Принимает список/кортеж или строку с разделителями «,» / «;».
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = re.split(r'[,;]', str(value))
    return [str(item).strip() for item in items if str(item).strip()]


def _resolve_scene(scene_name):
    """Находит сцену. Возвращает (scene, error_dict)."""
    scene, ambiguous = mcp_instance.find_scene(scene_name)
    if scene:
        return scene, None

    available = [s.get("title") for s in mcp_instance.load_scenes_from_db()]
    if ambiguous:
        return None, {
            "needs_scene": True,
            "question": "Какую сцену изменить?",
            "candidates": available,
            "message": "Уточните сцену: " + ", ".join(available),
        }
    return None, {
        "success": False,
        "error": "scene_not_found",
        "message": f"Сцена «{scene_name}» не найдена.",
        "available": available,
    }


def _resolve_scene_device(device):
    """Находит устройство(а) по псевдониму/синониму. Возвращает (specs, error_dict).

    Для сцены обычно нужно одно устройство, но допускаем и набор
    (например, «весь свет в комнате»). Использует общий резолвер, поэтому
    понимает комнаты, типы и уточнения.
    """
    result = mcp_instance.resolve_devices(device)
    if result.get("status") == "ok":
        specs = [d.get("_spec") for d in (result.get("devices") or []) if d.get("_spec")]
        if specs:
            return specs, None
    return None, clarification_response(result)


def _scene_actions_raw(scene):
    """Действия сцены в формате хранения: [{device_id, action, brightness}]."""
    raw = []
    for action in scene.get("actions") or []:
        if not action.get("device_id"):
            continue
        item = {"device_id": action.get("device_id"),
                "action": action.get("action") or "on"}
        if action.get("brightness") is not None:
            item["brightness"] = action.get("brightness")
        raw.append(item)
    return raw


def _describe_action(spec_title, action, brightness):
    """Описывает одно действие сцены по-русски."""
    if (action or "on") == "off":
        return f"{spec_title} — выключить"
    if brightness is not None:
        return f"{spec_title} — включить на {brightness}%"
    return f"{spec_title} — включить"


def _describe_scene(scene):
    """Человекочитаемый состав действий сцены."""
    parts = []
    for action in scene.get("actions") or []:
        device = action.get("device")
        title = (device or {}).get("title") if isinstance(device, dict) else None
        title = title or f"устройство {action.get('device_id')}"
        parts.append(_describe_action(title, action.get("action"), action.get("brightness")))
    return "; ".join(parts) if parts else "действий нет"


@mcp.tool()
def add_scene(scene_name: str, phrases=None, tts_feedback: bool = True) -> dict:
    """
    Создаёт новую сцену (группу).
    scene_name: название сцены, например «доброе утро».
    phrases: дополнительные фразы/синонимы (список или строка через запятую).
    Пример: «добавь сцену доброе утро».
    """
    name = (scene_name or "").strip()
    if not name:
        return {"success": False, "error": "bad_scene_name",
                "message": "Не указано название сцены."}

    existing, _ = mcp_instance.find_scene(name)
    if existing:
        return {"success": False, "error": "scene_exists",
                "scene": existing.get("title"),
                "message": f"Сцена «{existing.get('title')}» уже существует. "
                           f"Фразы можно добавить голосом: «добавь фразы в сцену…»."}

    extra = _as_list(phrases)
    scene_id = mcp_instance.create_scene(name, aliases=[name] + extra)
    if scene_id is None:
        return {"success": False, "error": "save_failed",
                "message": "Не удалось создать сцену в базе данных."}

    message = f"Сцена «{name}» создана"
    if extra:
        message += " (фразы: " + ", ".join(extra) + ")"
    if tts_feedback:
        mcp_instance.say_via_tts(message)
    logger.info(message)
    return {"success": True, "scene_id": scene_id, "scene": name,
            "phrases": extra, "message": message}


@mcp.tool()
def add_scene_phrases(scene_name: str, phrases, tts_feedback: bool = True) -> dict:
    """
    Добавляет фразы/синонимы к существующей сцене.
    phrases: список или строка через запятую, например «с добрым утром, просыпайся».
    """
    scene, error = _resolve_scene(scene_name)
    if error:
        return error

    names = _as_list(phrases)
    if not names:
        return {"success": False, "error": "no_phrases",
                "message": "Не указаны фразы для сцены."}

    row = mcp_instance.load_scene_row(scene["id"]) or {}
    merged = mcp_instance.merge_scene_aliases(row.get("aliase") or "", [scene.get("title")] + names)
    ok = mcp_instance.save_scene(scene["id"], row.get("title") or scene.get("title"),
                                 merged, row.get("enabled", True), _scene_actions_raw(scene))
    if not ok:
        return {"success": False, "error": "save_failed",
                "message": "Не удалось сохранить фразы сцены."}

    message = f"В сцену «{scene.get('title')}» добавлены фразы: " + ", ".join(names)
    if tts_feedback:
        mcp_instance.say_via_tts(message)
    logger.info(message)
    return {"success": True, "scene": scene.get("title"), "phrases": names,
            "aliases": merged, "message": message}


@mcp.tool()
def remove_scene_phrases(scene_name: str, phrases, tts_feedback: bool = True) -> dict:
    """
    Удаляет фразы/синонимы у сцены.
    phrases: список или строка через запятую.
    """
    scene, error = _resolve_scene(scene_name)
    if error:
        return error

    names = _as_list(phrases)
    if not names:
        return {"success": False, "error": "no_phrases",
                "message": "Не указаны фразы для удаления."}

    drop = {normalize_text(name) for name in names}
    row = mcp_instance.load_scene_row(scene["id"]) or {}
    kept = [part for part in mcp_instance.split_aliases_raw(row.get("aliase") or "")
            if normalize_text(part) not in drop]
    ok = mcp_instance.save_scene(scene["id"], row.get("title") or scene.get("title"),
                                 mcp_instance.format_scene_aliases(kept),
                                 row.get("enabled", True), _scene_actions_raw(scene))
    if not ok:
        return {"success": False, "error": "save_failed",
                "message": "Не удалось удалить фразы сцены."}

    message = f"Из сцены «{scene.get('title')}» удалены фразы: " + ", ".join(names)
    if tts_feedback:
        mcp_instance.say_via_tts(message)
    logger.info(message)
    return {"success": True, "scene": scene.get("title"), "phrases": names,
            "aliases": " ".join(kept), "message": message}


@mcp.tool()
def rename_scene(scene_name: str, new_name: str, tts_feedback: bool = True) -> dict:
    """
    Переименовывает сцену. Старое название остаётся синонимом.
    """
    scene, error = _resolve_scene(scene_name)
    if error:
        return error

    new_name = (new_name or "").strip()
    if not new_name:
        return {"success": False, "error": "bad_name",
                "message": "Не указано новое название сцены."}

    row = mcp_instance.load_scene_row(scene["id"]) or {}
    merged = mcp_instance.merge_scene_aliases(row.get("aliase") or scene.get("title"), [new_name])
    ok = mcp_instance.save_scene(scene["id"], new_name, merged,
                                 row.get("enabled", True), _scene_actions_raw(scene))
    if not ok:
        return {"success": False, "error": "save_failed",
                "message": "Не удалось переименовать сцену."}

    message = f"Сцена «{scene.get('title')}» переименована в «{new_name}»"
    if tts_feedback:
        mcp_instance.say_via_tts(message)
    logger.info(message)
    return {"success": True, "scene": new_name, "old_scene": scene.get("title"),
            "message": message}


@mcp.tool()
def delete_scene(scene_name: str, tts_feedback: bool = True) -> dict:
    """
    Удаляет сцену (группу) целиком.
    Пример: «удали сцену доброе утро».
    """
    scene, error = _resolve_scene(scene_name)
    if error:
        return error

    if not mcp_instance.delete_scene(scene["id"]):
        return {"success": False, "error": "delete_failed",
                "message": "Не удалось удалить сцену."}

    message = f"Сцена «{scene.get('title')}» удалена"
    if tts_feedback:
        mcp_instance.say_via_tts(message)
    logger.info(message)
    return {"success": True, "scene": scene.get("title"), "message": message}


@mcp.tool()
def set_scene_enabled(scene_name: str, enabled: bool, tts_feedback: bool = True) -> dict:
    """
    Включает или выключает сцену (выключенную сцену нельзя запустить).
    """
    scene, error = _resolve_scene(scene_name)
    if error:
        return error

    row = mcp_instance.load_scene_row(scene["id"]) or {}
    ok = mcp_instance.save_scene(scene["id"], row.get("title") or scene.get("title"),
                                 row.get("aliase") or scene.get("title"),
                                 bool(enabled), _scene_actions_raw(scene))
    if not ok:
        return {"success": False, "error": "save_failed",
                "message": "Не удалось изменить состояние сцены."}

    message = f"Сцена «{scene.get('title')}» " + ("включена" if enabled else "выключена")
    if tts_feedback:
        mcp_instance.say_via_tts(message)
    logger.info(message)
    return {"success": True, "scene": scene.get("title"), "enabled": bool(enabled),
            "message": message}


@mcp.tool()
def add_device_to_scene(scene_name: str, device: str, action: str = "включи",
                        brightness: int = None, tts_feedback: bool = True) -> dict:
    """
    Добавляет устройство в сцену (если устройство уже есть — обновляет действие).
    scene_name: сцена, например «доброе утро».
    device: псевдоним (синоним) устройства, например «нижний свет».
    action: «включи» или «выключи».
    brightness: яркость в процентах (0-100), необязательно.
    Пример: «добавь нижний свет в сцену доброе утро, включать на 40%».
    """
    scene, error = _resolve_scene(scene_name)
    if error:
        return error

    specs, error = _resolve_scene_device(device)
    if error:
        return error

    is_on = _is_on_action(action)
    brightness_value = None
    if brightness is not None and str(brightness).strip() != "":
        try:
            brightness_value = max(0, min(100, int(brightness)))
        except (TypeError, ValueError):
            brightness_value = None

    added_ids = {spec.get("id") for spec in specs}
    actions = [a for a in _scene_actions_raw(scene) if a.get("device_id") not in added_ids]
    details = []
    for spec in specs:
        item = {"device_id": spec.get("id"), "action": "on" if is_on else "off"}
        if brightness_value is not None:
            item["brightness"] = brightness_value
        actions.append(item)
        details.append(_describe_action(spec.get("title") or device, item["action"],
                                        item.get("brightness")))

    row = mcp_instance.load_scene_row(scene["id"]) or {}
    ok = mcp_instance.save_scene(scene["id"], row.get("title") or scene.get("title"),
                                 row.get("aliase") or scene.get("title"),
                                 row.get("enabled", True), actions)
    if not ok:
        return {"success": False, "error": "save_failed",
                "message": "Не удалось сохранить сцену."}

    message = f"В сцену «{scene.get('title')}» добавлено: " + "; ".join(details)
    if tts_feedback:
        mcp_instance.say_via_tts(message)
    logger.info(message)
    return {"success": True, "scene": scene.get("title"),
            "devices": [spec.get("title") or device for spec in specs],
            "action": "on" if is_on else "off", "brightness": brightness_value,
            "message": message}


@mcp.tool()
def remove_device_from_scene(scene_name: str, device: str, tts_feedback: bool = True) -> dict:
    """
    Удаляет устройство из сцены.
    Пример: «убери нижний свет из сцены доброе утро».
    """
    scene, error = _resolve_scene(scene_name)
    if error:
        return error

    specs, error = _resolve_scene_device(device)
    if error:
        return error

    ids = {spec.get("id") for spec in specs}
    current = _scene_actions_raw(scene)
    actions = [a for a in current if a.get("device_id") not in ids]
    titles = ", ".join(spec.get("title") or device for spec in specs)
    if len(actions) == len(current):
        return {"success": False, "error": "not_in_scene",
                "message": f"Устройства «{titles}» нет в сцене «{scene.get('title')}»."}

    row = mcp_instance.load_scene_row(scene["id"]) or {}
    ok = mcp_instance.save_scene(scene["id"], row.get("title") or scene.get("title"),
                                 row.get("aliase") or scene.get("title"),
                                 row.get("enabled", True), actions)
    if not ok:
        return {"success": False, "error": "save_failed",
                "message": "Не удалось сохранить сцену."}

    message = f"Из сцены «{scene.get('title')}» убрано устройство: {titles}"
    if tts_feedback:
        mcp_instance.say_via_tts(message)
    logger.info(message)
    return {"success": True, "scene": scene.get("title"), "device": titles,
            "message": message}


# === Запуск ===
if __name__ == "__main__":
    mcp.run(transport="stdio")