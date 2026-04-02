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
from mcp_class import MajorDoMoMCP

# === Настройка ===
log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper())
logging.basicConfig(stream=sys.stderr, level=log_level, format="%(levelname)s: %(message)s")
logger = logging.getLogger("mcp_majordomo")

# === Создаем экземпляр класса ===
mcp_instance = MajorDoMoMCP()

# === MCP-сервер ===
mcp = FastMCP("Majordomo Universal")

mcp_instance.debmes(f"Starting servers: mcp", "mcp")

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
def list_devices() -> dict:
    """Список всех устройств"""
    aliases = mcp_instance.load_aliases_from_db()
    #logger.info(f"Все алиасы: {list(aliases.keys())}")
    return {"devices": list(aliases.keys())}

@mcp.tool()
def list_rooms() -> dict:
    """Список комнат из MajorDoMo"""
    resp = mcp_instance.call_majordomo("GET", "rooms")
    if resp and resp.status_code == 200:
        try:
            return {"rooms": resp.json()}
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

# === НОВЫЕ МЕТОДЫ (с TTS, поддержкой дубликатов, логированием и учётом типа) ===
@mcp.tool()
def control_device(device_query: str, action: str, tts_feedback: bool = True) -> dict:
    """
    Управление реле (тип 'light') с TTS и поддержкой дублирующихся алиасов.
    Используй, когда пользователь говорит: 'включи свет в комнате отдыха', 'выключи улицу'.
    Не используй, если пользователь говорит 'через 1 минуту' или 'в 15:30'.
    """
    norm_query = mcp_instance.normalize_query(device_query)
    logger.info(f"Запрос: '{device_query}' → нормализовано: '{norm_query}'")

    # Ищем устройство в категориях свет/устройств с типом light
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_query,
        required_type=["light"]
    )
    if not device_spec:
        # Формируем список ТОЛЬКО релевантных алиасов (реле)
        aliases = mcp_instance.load_aliases_from_db()
        relevant_aliases = []
        for alias_name, specs in aliases.items():
            for spec in specs:
                if spec["category"] in ["light"] and spec["type"] == "devices.types.light":
                    relevant_aliases.append(alias_name)
                    break
        available = ", ".join(sorted(set(relevant_aliases)))
        logger.info(f"Не найдено. Доступные реле: {available}")
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> control_device: {device_query} [FAILED] Details: {{'error': 'Устройство (реле) не найдено', 'available': '{available}'}}", "mcp")
        return {"error": f"Не найдено (реле): '{device_query}'. Доступные: {available}"}

    # Гибкая обработка действия
    action_lower = action.lower()
    if any(word in action_lower for word in ["включи", "включить", "on", "1", "да", "зажги", "активируй", "включи свет"]):
        value = "1"
        state_word = "включён"
    elif any(word in action_lower for word in ["выключи", "выключить", "off", "0", "нет", "потуши", "деактивируй", "выключи свет"]):
        value = "0"
        state_word = "выключен"
    else:
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> control_device: {device_query} [FAILED] Details: {{'error': 'Неизвестное действие: '{action}'}}", "mcp")
        return {"error": f"Неизвестное действие: '{action}'. Используйте 'включи' или 'выключи'."}

    # Выполнение команды
    logger.info(f"Свет: {device_spec['object']}.{device_spec['property']}={value}")
    resp = mcp_instance.call_majordomo("POST", f"data/{device_spec['object']}.{device_spec['property']}", data={"data": value})
    if resp and resp.status_code == 200:
        if mcp_instance.debug:
            mcp_instance.debmes(f"Свет: {device_spec['object']}.{device_spec['property']}={value}", "mcp")
        if tts_feedback:
            mcp_instance.say_via_tts(f"Свет в {norm_query} {state_word}")
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> control_device: {norm_query} [SUCCESS] Details: {{'state': '{state_word}', 'device_query': '{device_query}', 'action': '{action}'}}", "mcp")
        return {"success": True, "target": norm_query, "state": state_word}
    else:
        error_msg = f"MajorDoMo error: {resp.status_code if resp else 'timeout'}"
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> control_device: {norm_query} [FAILED] Details: {{'error': '{error_msg}', 'device_query': '{device_query}'}}", "mcp")
        return {"error": error_msg}

@mcp.tool()
def get_device_status(device_query: str, tts_feedback: bool = True) -> dict:
    """Статус реле (тип 'light') с TTS и поддержкой дубликующихся алиасов."""
    norm_query = mcp_instance.normalize_query(device_query)
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_query,
        preferred_categories=["light", "devices", "свет", "устройства"],  # Обновлено для БД
        required_type="devices.types.light"  # Только реле
    )
    if not device_spec:
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> get_device_status: {device_query} [FAILED] Details: {{'error': 'Устройство (реле) не найдено'}}", "mcp")
        return {"error": f"Не найдено (реле): '{device_query}'"}

    resp = mcp_instance.call_majordomo("GET", f"data/{device_spec['object']}.{device_spec['property']}")
    if resp and resp.status_code == 200:
        try:
            value = resp.json().get("data", resp.text.strip())
        except:
            value = resp.text.strip()
        value_str = str(value)
        status = "включено" if value_str == "1" else "выключено"
        if tts_feedback:
            mcp_instance.say_via_tts(f"Свет в {norm_query} {status}")
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> get_device_status: {norm_query} [SUCCESS] Details: {{'status': '{status}', 'value': '{value}', 'device_query': '{device_query}'}}", "mcp")
        return {"device": norm_query, "status": status}
    error_msg = f"MajorDoMo error: {resp.status_code if resp else 'timeout'}"
    # Логируем через debmes при debug
    if mcp_instance.debug:
        mcp_instance.debmes(f"[mcp] xiaozhi -> get_device_status: {norm_query} [FAILED] Details: {{'error': '{error_msg}', 'device_query': '{device_query}'}}", "mcp")
    return {"error": error_msg}

@mcp.tool()
def get_sensor_value(sensor_query: str, unit: str = "", tts_feedback: bool = True) -> dict:
    """
    Получает текущее значение сенсора (температура, влажность, давление и т.д.) с возможностью озвучивания через TTS.
    Автоматически используется при вопросах вроде "какая температура в комнате отдыха?", "сколько градусов в бане?", "влажность в зале?" и т.п.
    unit: единицы измерения для озвучивания (например, "градусов", "процентов", "Паскаль").
    """
    norm_query = mcp_instance.normalize_query(sensor_query)
    logger.info(f"Запрос сенсора: '{sensor_query}' → нормализовано: '{norm_query}'")

    # Загружаем все алиасы
    aliases = mcp_instance.load_aliases_from_db()
    #logger.info(f"Все алиасы: {list(aliases.keys())}")

    # Ищем ТОЛЬКО сенсоры с нормализованным запросом
    if norm_query in aliases:
        #logger.info(f"Найдены алиасы для '{norm_query}': {aliases[norm_query]}")
        # Изменяем условие - теперь ищем как 'sensor', так и 'devices.types.sensor'
        sensor_specs = [spec for spec in aliases[norm_query]
                       if spec["type"] == "devices.types.sensor" or spec["type"] == "sensor"]
        #logger.info(f"Сенсоры среди них: {sensor_specs}")
        if sensor_specs:
            device_spec = sensor_specs[0]  # Берем первый сенсор
            #logger.info(f"Выбран сенсор: {device_spec}")
        else:
            device_spec = None
    else:
        logger.info(f"Нет алиасов для '{norm_query}' в списке: {list(aliases.keys())}")
        device_spec = None

    if not device_spec:
        # Формируем список ТОЛЬКО сенсоров
        relevant_aliases = []
        for alias_name, specs in aliases.items():
            for spec in specs:
                if spec["type"] == "devices.types.sensor" or spec["type"] == "sensor":
                    relevant_aliases.append(alias_name)
                    break
        available = ", ".join(sorted(set(relevant_aliases)))
        logger.info(f"Сенсор не найден. Доступные: {available}")
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> get_sensor_value: {sensor_query} [FAILED] Details: {{'error': 'Сенсор не найден', 'available': '{available}'}}", "mcp")
        return {"error": f"Сенсор не найден: '{sensor_query}'. Доступные: {available}"}

    resp = mcp_instance.call_majordomo("GET", f"data/{device_spec['object']}.{device_spec['property']}")
    if resp and resp.status_code == 200:
        try:
            value = resp.json().get("data", resp.text.strip())
        except:
            value = resp.text.strip()
        if tts_feedback:
            tts_success = mcp_instance.say_via_tts(f"В {norm_query} {value} {unit}")
            logger.info(f"В {norm_query} {value} {unit}")
            if not tts_success:
                logger.warning(f"TTS не удалось выполнить для {norm_query}")
            else:
                logger.info(f"TTS успешно выполнено")
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> get_sensor_value: {norm_query} [SUCCESS] Details: {{'value': '{value}', 'unit': '{unit}', 'sensor_query': '{sensor_query}'}}", "mcp")
        return {"sensor": norm_query, "value": value, "unit": unit}
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

    # Ищем устройство с типом device
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_query,
        required_type="devices.types.device"  # Только устройства с параметрами
    )
    if not device_spec:
        # Формируем список ТОЛЬКО устройств с параметрами
        aliases = mcp_instance.load_aliases_from_db()
        relevant_aliases = []
        for alias_name, specs in aliases.items():
            for spec in specs:
                if spec["type"] == "devices.types.device":
                    relevant_aliases.append(alias_name)
                    break
        available = ", ".join(sorted(set(relevant_aliases)))
        logger.info(f"Устройство (device) не найдено. Доступные: {available}")
        # Логируем через debmes при debug
        if mcp_instance.debug:
            mcp_instance.debmes(f"[mcp] xiaozhi -> set_device_parameter: {device_query} [FAILED] Details: {{'error': 'Устройство (device) не найдено', 'available': '{available}'}}", "mcp")
        return {"error": f"Устройство (device) не найдено: '{device_query}'. Доступные: {available}"}

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

@mcp.tool()
def add_scheduler_task(time_str: str, device: str, action: str, repeat_days: list = None) -> dict:
    """
    Добавляет задание в планировщик.
    time_str: "HH:MM" (например, "17:15")
    device: имя устройства (например, "улица")
    action: "включи" или "выключи"
    repeat_days: ["mon", "tue", ...] или None (одноразовое)
    """
    schedule = mcp_instance.load_schedule_from_db()
    # Генерируем ID на основе времени и устройства
    task_id = f"voice_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{device.replace(' ', '_')}"

    # === НОВОЕ: Если repeat_days не указан, считаем одноразовым ===
    if repeat_days is None:
        repeat_days = ["once"]
    # ===

    # Найдем спецификацию устройства для получения связанных объекта и свойства
    device_spec = mcp_instance.find_device_by_category_and_type(device)
    action_data_dict = {}
    if device_spec:
        # Определяем тип действия (on/off) в зависимости от слова в команде
        action_type = "on" if action.lower() in ["включи", "включить", "on", "1", "да"] else "off"
        action_data_dict = {
            "type": action_type,
            "linked_object": device_spec["object"],
            "linked_property": device_spec["property"]
        }
    else:
        # Если не найдено спецификации, используем стандартную структуру
        action_type = "on" if action.lower() in ["включи", "включить", "on", "1", "да"] else "off"
        action_data_dict = {
            "type": action_type,
            "linked_object": device,
            "linked_property": "status"
        }

    new_task = {
        "id": task_id,
        "enabled": True,
        "description": f"Голосовое задание: {action} {device}",
        "time": time_str,
        "days": repeat_days,  # Теперь может быть и постоянным
        "action": {
            "type": "device",
            "device": device,
            "state": action,
            "data": action_data_dict  # Добавляем данные действия
        }
    }
    schedule.append(new_task)
    mcp_instance.save_schedule_to_db(schedule)
#    mcp_instance.reload_scheduler()

    # Логируем через debmes при debug
    if mcp_instance.debug:
        mcp_instance.debmes(f"[mcp] xiaozhi -> add_scheduler_task: {task_id} [SUCCESS] Details: {{'time': '{time_str}', 'device': '{device}', 'action': '{action}', 'repeat_days': '{repeat_days}', 'action_data': '{action_data_dict}'}}", "mcp")
    return {"success": True, "message": f"Задание добавлено: {action} {device} в {time_str} {'каждый день' if repeat_days == ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] else 'по дням: ' + ', '.join(repeat_days)}"}

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

    mcp_instance.save_schedule_to_db(schedule)
#    mcp_instance.reload_scheduler()

    # Логируем через debmes при debug
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

    # Оставляем только отключённые задания (если такие есть)
    schedule = [task for task in schedule if not task["enabled"]]
    mcp_instance.save_schedule_to_db(schedule)
#    mcp_instance.reload_scheduler()

    # Логируем через debmes при debug
    if mcp_instance.debug:
        mcp_instance.debmes(f"[mcp] xiaozhi -> delete_all_scheduler_tasks: all [SUCCESS] Details: {{'deleted_count': '{original_count}'}}", "mcp")
    return {"success": True, "message": f"Все задания ({original_count}) удалены"}

@mcp.tool()
def list_scheduler_tasks() -> dict:
    """
    Возвращает список текущих заданий.
    """
    schedule = mcp_instance.load_schedule_from_db()
    active_tasks = [task for task in schedule if task["enabled"]]
    if not active_tasks:
        message = "Нет активных заданий."
    else:
        task_list = []
        for task in active_tasks:
            time = task.get("time", "неизвестно")
            device = task["action"].get("device", "неизвестно")
            action = task["action"].get("state", "неизвестно")
            desc = task.get("description", f"{action} {device}")
            task_list.append(f"{time} — {desc}")
        message = "Активные задания: " + "; ".join(task_list)

    # Логируем через debmes при debug
    if mcp_instance.debug:
        mcp_instance.debmes(f"[mcp] xiaozhi -> list_scheduler_tasks: all [SUCCESS] Details: {{'count': '{len(active_tasks)}'}}", "mcp")
    return {"tasks": active_tasks, "message": message}

@mcp.tool()
def add_temporary_scheduler_task(minutes_from_now: int, device: str, action: str) -> dict:
    """
    Добавляет задание, которое выполнится через N минут.
    minutes_from_now: int
    device: имя устройства
    action: "включи" или "выключи"
    """
    future_time = datetime.now() + timedelta(minutes=minutes_from_now)
    time_str = future_time.strftime("%H:%M")
    schedule = mcp_instance.load_schedule_from_db()
    task_id = f"voice_temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{device.replace(' ', '_')}"

    # Найдем спецификацию устройства для получения связанных объекта и свойства
    device_spec = mcp_instance.find_device_by_category_and_type(device)
    action_data_dict = {}
    if device_spec:
        # Определяем тип действия (on/off) в зависимости от слова в команде
        action_type = "on" if action.lower() in ["включи", "включить", "on", "1", "да"] else "off"
        action_data_dict = {
            "type": action_type,
            "linked_object": device_spec["object"],
            "linked_property": device_spec["property"]
        }
    else:
        # Если не найдено спецификации, используем стандартную структуру
        action_type = "on" if action.lower() in ["включи", "включить", "on", "1", "да"] else "off"
        action_data_dict = {
            "type": action_type,
            "linked_object": device,
            "linked_property": "status"
        }

    new_task = {
        "id": task_id,
        "enabled": True,
        "description": f"Временное задание: {action} {device} через {minutes_from_now} мин",
        "time": time_str,
        "days": ["once"],  # Одноразовое
        "action": {
            "type": "device",
            "device": device,
            "state": action,
            "data": action_data_dict  # Добавляем данные действия
        }
    }
    schedule.append(new_task)
    mcp_instance.save_schedule_to_db(schedule)
#    mcp_instance.reload_scheduler()

    # Логируем через debmes при debug
    if mcp_instance.debug:
        mcp_instance.debmes(f"[mcp] xiaozhi -> add_temporary_scheduler_task: {task_id} [SUCCESS] Details: {{'time': '{time_str}', 'device': '{device}', 'action': '{action}', 'minutes_delay': '{minutes_from_now}', 'action_data': '{action_data_dict}'}}", "mcp")
    return {"success": True, "message": f"Задание добавлено: {action} {device} через {minutes_from_now} минут"}

@mcp.tool()
def control_openable(device_query: str, action: str, percent: int = None, tts_feedback: bool = True) -> dict:
    """
    Управление устройствами типа openable (ворота, шторы, жалюзи).
    action: 'открой', 'закрой', 'приоткрой', 'останови', 'продолжи'
    percent: для штор, жалюзи, ворот, дверей (0-100)
    """
    norm_query = mcp_instance.normalize_query(device_query)
    logger.info(f"Запрос управления openable: '{device_query}' → нормализовано: '{norm_query}', действие: '{action}', процент: {percent}")

    # Ищем устройство типа openable
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_query,
        required_type=["openable", "openable.curtain", "devices.types.openable", "devices.types.openable.curtain"]
    )
    
    if not device_spec:
        # Формируем список доступных openable устройств
        aliases = mcp_instance.load_aliases_from_db()
        relevant_aliases = []
        for alias_name, specs in aliases.items():
            for spec in specs:
                if spec["type"] in ["openable", "openable.curtain", "devices.types.openable", "devices.types.openable.curtain"]:
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
            value_on = "0"
            object_name = pause_linked_object
            property_name = pause_linked_property
        
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

    # Отправляем команду в pause свойство, если значение задано
    if value_on is not None and pause_linked_object and pause_linked_property:
        logger.info(f"Отправляю в pause: {pause_linked_object}.{pause_linked_property} = {value_on}")
        resp_pause = mcp_instance.call_majordomo("POST", f"data/{pause_linked_object}.{pause_linked_property}", data={"data": value_on})
        if resp_pause and resp_pause.status_code != 200:
            results.append(f"MajorDoMo error (on): {resp_pause.status_code if resp_pause else 'timeout'}")

    # Отправляем команду в on свойство, если значение задано
    if value_on is not None and on_linked_object and on_linked_property:
        logger.info(f"Отправляю в on: {on_linked_object}.{on_linked_property} = {value_on}")
        resp_on = mcp_instance.call_majordomo("POST", f"data/{on_linked_object}.{on_linked_property}", data={"data": value_on})
        if resp_on and resp_on.status_code != 200:
            results.append(f"MajorDoMo error (on): {resp_on.status_code if resp_on else 'timeout'}")
    
    # Отправляем команду в open свойство, если значение задано
    if value_open is not None and open_linked_object and open_linked_property:
        logger.info(f"Отправляю в open: {open_linked_object}.{open_linked_property} = {value_open}")
        resp_open = mcp_instance.call_majordomo("POST", f"data/{open_linked_object}.{open_linked_property}", data={"data": value_open})
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
        return {"success": True, "target": norm_query, "command": "set_position", "value_on": value_on, "value_open": value_open}

@mcp.tool()
def get_openable_status(device_query: str, tts_feedback: bool = True) -> dict:
    """
    Получение статуса openable устройства (ворота, шторы).
    """
    norm_query = mcp_instance.normalize_query(device_query)
    logger.info(f"Запрос статуса openable: '{device_query}' → нормализовано: '{norm_query}'")

    # Ищем устройство типа openable
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_query,
        preferred_categories=["openable", "openable.curtain"]
    )
    
    if not device_spec:
        # Если не нашли по категории, ищем по типу
        aliases = mcp_instance.load_aliases_from_db()
        if norm_query in aliases:
            openable_specs = [spec for spec in aliases[norm_query] 
                             if spec["type"] in ["openable", "openable.curtain", "devices.types.openable", "devices.types.openable.curtain"]]
            if openable_specs:
                device_spec = openable_specs[0]
    
    if not device_spec:
        # Формируем список доступных openable устройств
        aliases = mcp_instance.load_aliases_from_db()
        relevant_aliases = []
        for alias_name, specs in aliases.items():
            for spec in specs:
                if spec["type"] in ["openable", "openable.curtain", "devices.types.openable", "devices.types.openable.curtain"]:
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
            return {"device": norm_query, "status": status, "raw_value": value}
    
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
                status = "открыто"
            elif value == "0":
                status = "закрыто"
            else:
                status = f"в состоянии {value}"

            if tts_feedback:
                mcp_instance.say_via_tts(f"{device_query.capitalize()} {status}")
            return {"device": norm_query, "status": status, "raw_value": value}
    
    error_msg = f"MajorDoMo error: не удалось получить статус устройства"
    return {"error": error_msg}

@mcp.tool()
def control_thermostat(device_query: str, action: str, temperature: float = None, mode: str = None, tts_feedback: bool = True) -> dict:
    """
    Управление термостатами (отопление, баня, теплый пол, батарея).
    action: 'включи', 'выключи'
    temperature: для установки температуры (если указана)
    mode: 'thermostat', 'keep_warm' для специальных режимов
    """
    norm_query = mcp_instance.normalize_query(device_query)
    logger.info(f"Запрос управления термостатом: '{device_query}' → нормализовано: '{norm_query}', действие: '{action}', температура: {temperature}, режим: {mode}")

    # Ищем устройство типа thermostat
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_query,
        required_type=["thermostat"]
    )
    logger.info(f"device_spec: {device_spec}")
    if not device_spec:
        # Формируем список доступных термостатов
        aliases = mcp_instance.load_aliases_from_db()
        logger.info(f"Алиасы: {aliases}")
        relevant_aliases = []
        for alias_name, specs in aliases.items():
            for spec in specs:
                if spec["type"] in ["thermostat", "devices.types.thermostat"]:
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
    if temperature is not None:
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
    
    return {"success": True, "target": norm_query, "actions": responses}

@mcp.tool()
def get_thermostat_status(device_query: str, tts_feedback: bool = True) -> dict:
    """
    Получение статуса термостата.
    """
    norm_query = mcp_instance.normalize_query(device_query)
    logger.info(f"Запрос статуса термостата: '{device_query}' → нормализовано: '{norm_query}'")

    # Ищем устройство типа thermostat
    device_spec = mcp_instance.find_device_by_category_and_type(
        norm_query,
        required_type=["thermostat", "devices.types.thermostat"]
    )
    
    if not device_spec:
        # Формируем список доступных термостатов
        aliases = mcp_instance.load_aliases_from_db()
        relevant_aliases = []
        for alias_name, specs in aliases.items():
            for spec in specs:
                if spec["type"] in ["thermostat", "devices.types.thermostat"]:
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
    
    return {"device": norm_query, "status": status_info}

# === Запуск ===
if __name__ == "__main__":
    mcp.run(transport="stdio")