#!/usr/bin/env python3
"""
Класс для работы с MajorDoMo MCP
"""
import sys
import os
import json
import logging
import re
import time
import requests
from datetime import datetime, timedelta

log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper())
logging.basicConfig(stream=sys.stderr, level=log_level, format="%(levelname)s: %(message)s")
logger = logging.getLogger("mcp_majordomo")

# Сколько секунд держать в памяти индекс алиасов (mcp_devices).
# Спасает от повторных запросов к БД внутри одного обращения LLM,
# при этом изменения в устройствах подхватываются через несколько секунд.
ALIASES_CACHE_TTL = 5

# Кэш списка комнат держим дольше: locations меняются редко.
ROOMS_CACHE_TTL = 60

# Ревизия Python-части модуля. Пишется в лог при старте моста: по логу видно,
# какой именно код реально запущен. Версия модуля в интерфейсе — это PHP
# (mcp.class.php), файлы lib/*.py подхватываются только при перезапуске моста.
LIB_REVISION = '2.01.27'

# Сцены/группы. Таблица создаётся сама (в т.ч. на уже установленных модулях).
SCENES_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS mcp_scenes ("
    " ID int(10) unsigned NOT NULL auto_increment,"
    " TITLE varchar(255) NOT NULL DEFAULT '',"
    " ALIACE varchar(255) NOT NULL DEFAULT '',"
    " ENABLED tinyint(1) NOT NULL DEFAULT 1,"
    " ACTIONS text,"
    " CREATED datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,"
    " UPDATED timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
    " PRIMARY KEY (ID)"
    ")"
)

# Планировщик. Таблица создаётся/дополняется лениво (в т.ч. на уже
# установленных модулях — добавляется колонка LAST_RUN).
SCHEDULE_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS mcp_schedule ("
    " ID int(10) unsigned NOT NULL auto_increment,"
    " TASK_ID varchar(255) NOT NULL DEFAULT '',"
    " TITLE varchar(255) NOT NULL DEFAULT '',"
    " ENABLED tinyint(1) NOT NULL DEFAULT 0,"
    " SCHEDULE_TIME varchar(10) NOT NULL DEFAULT '',"
    " SCHEDULE_DAYS varchar(255) NOT NULL DEFAULT '',"
    " SCHEDULE_DATE date DEFAULT NULL,"
    " ACTION_TYPE varchar(50) NOT NULL DEFAULT '',"
    " ACTION_TARGET varchar(255) NOT NULL DEFAULT '',"
    " ACTION_STATE varchar(100) NOT NULL DEFAULT '',"
    " ACTION_DATA text,"
    " LAST_RUN datetime DEFAULT NULL,"
    " CREATED datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,"
    " UPDATED timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
    " PRIMARY KEY (ID)"
    ")"
)

# Определение колонки голосом (кнопка «Определить колонку» в форме устройства).
# Одна строка (ID=1): «вооружение» ожидания ставит PHP, результат пишет
# инструмент identify_speaker, опрашивает PHP и отдаёт форме.
DETECT_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS mcp_detect ("
    " ID int unsigned NOT NULL PRIMARY KEY,"
    " TS bigint NOT NULL DEFAULT 0,"
    " TARGET_ID int NOT NULL DEFAULT 0,"
    " TITLE varchar(255) NOT NULL DEFAULT '',"
    " ALIACE varchar(255) NOT NULL DEFAULT '',"
    " LOCATION varchar(255) NOT NULL DEFAULT '',"
    " RESULT varchar(32) NOT NULL DEFAULT '',"
    " RESULT_DEVICE_ID varchar(64) NOT NULL DEFAULT '',"
    " RESULT_ROOM varchar(255) NOT NULL DEFAULT '',"
    " RESULT_TITLE varchar(255) NOT NULL DEFAULT '',"
    " NEW_ID int NOT NULL DEFAULT 0"
    ")"
)

# Свежесть вооружения: кнопка ждёт ответ 10 сек, даём небольшой запас.
DETECT_WINDOW_SECONDS = 15

# Очередь команд колонкам (озвучка/громкость). Колонка XiaoZhi — клиент MCP:
# модуль не может «толкнуть» в неё звук, поэтому когда прямого канала нет,
# команда ставится в очередь, а следующий же инструмент, вызванный с этой
# колонки (в его аргументах приходит device_id), отдаёт накопленные действия.
SPEAKER_QUEUE_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS mcp_speaker_queue ("
    " ID int unsigned NOT NULL AUTO_INCREMENT,"
    " TARGET_ID varchar(64) NOT NULL DEFAULT '',"
    " ROOM varchar(255) NOT NULL DEFAULT '',"
    " TITLE varchar(255) NOT NULL DEFAULT '',"
    " COMMAND varchar(16) NOT NULL DEFAULT 'say',"
    " MESSAGE text,"
    " VALUE varchar(32) NOT NULL DEFAULT '',"
    " CHANNEL varchar(16) NOT NULL DEFAULT '',"
    " STATUS varchar(16) NOT NULL DEFAULT 'pending',"
    " CREATED datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,"
    " DELIVERED datetime DEFAULT NULL,"
    " ERROR varchar(255) NOT NULL DEFAULT '',"
    " PRIMARY KEY (ID)"
    ")"
)

# Громкость, которую возвращаем при «включи колонку» (у XiaoZhi нет отдельного
# питания; выключение = громкость 0, включение = эта величина).
SPEAKER_DEFAULT_VOLUME = 60


def table_engine_suffix(cursor):
    """DDL-суффикс с движком таблицы.

    InnoDB предпочтителен, но на сборках СУБД с отключённым InnoDB
    CREATE TABLE ... ENGINE=InnoDB падает с ошибкой 1286 «Unknown storage
    engine 'InnoDB'». В этом случае движок не указываем — сервер возьмёт
    свой по умолчанию (обычно MyISAM).
    """
    try:
        cursor.execute(
            "SELECT SUPPORT FROM information_schema.ENGINES WHERE ENGINE = 'InnoDB'"
        )
        row = cursor.fetchone()
    except Exception:
        row = None
    support = str(row[0]).upper() if row else ""
    if support and support != "NO":
        return " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    return " DEFAULT CHARSET=utf8mb4"


def safe_json_loads(raw, default):
    """json.loads с безопасным fallback (для TRAITS/CONFIG/ACTIONS из БД)."""
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except Exception:
        return default
    return parsed if parsed is not None else default


def parse_aliases(aliace):
    """Разбирает поле ALIACE: разделители «,» или «;», trim, регистр, дедупликация.

    Название устройства (TITLE) сюда НЕ добавляется: оно информационное и не
    участвует в распознавании.
    """
    if not aliace:
        return []
    raw_parts = re.split(r'[,;]', str(aliace))
    names = []
    seen = set()
    for part in raw_parts:
        name = normalize_text(part)
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def iter_traits(traits):
    """Единый обход TRAITS: поддержаны dict и list.</summary>"""
    if isinstance(traits, dict):
        items = list(traits.values())
    elif isinstance(traits, list):
        items = traits
    else:
        items = []
    return [item for item in items if isinstance(item, dict)]


def find_linked_trait(traits, trait_types):
    """Возвращает {object, property, type} для первого трейта нужного вида."""
    wanted = {normalize_device_type(t) for t in (trait_types or [])}
    for trait in iter_traits(traits):
        trait_kind = trait.get('type') or ''
        if wanted and normalize_device_type(trait_kind) not in wanted:
            continue
        linked_object = (trait.get('linked_object') or '').strip()
        linked_property = (trait.get('linked_property') or '').strip()
        if linked_object and linked_property:
            return {"object": linked_object, "property": linked_property, "type": trait_kind}
    return None


def first_linked_trait(traits):
    """Объект и свойство первого непустого трейта (для основного канала управления)."""
    trait = find_linked_trait(traits, None)
    if not trait:
        return None, None
    return trait["object"], trait["property"]

DEVICES_TYPES_PREFIX = "devices.types."

# Типы устройств, умеющие озвучивать текст (колонки и медиаустройства).
# Используются для TTS-ответов и для «передай фразу на колонку в комнате X».
SPEAKER_TYPES = [
    "audio.speaker", "speaker",
    "media_device", "media_device.receiver",
    "media_device.tv", "media_device.tv_box",
]

# Типы «колонок-комнат» (XiaoZhi): у них нет управляющих трейтов, они нужны
# только для привязки комнаты. Такие устройства остаются в списке устройств
# (list_devices) даже без linked-объекта, чтобы их было видно в комнате.
ROOM_BINDING_TYPES = ["audio.speaker", "speaker"]

# --- Семантика голосового управления -------------------------------------
# Словарь «слово в запросе -> тип устройства» (правило: если в фразе названо
# типовое слово («свет», «шторы»), ищем по типу устройства, а не по псевдониму).
# Ключи — СТЕМЫ (stem() ниже), поэтому падежи («света», «светом») попадают
# в тот же ключ автоматически.
TYPE_WORDS = {
    "свет": "light",
    "люстр": "light",
    "ламп": "light",
    "лампочк": "light",
    "светильник": "light",
    "освещен": "light",
    "подсветк": "light",
    "розетк": "socket",
    "штор": "openable.curtain",
    "жалюз": "openable.curtain",
    "занавес": "openable.curtain",
    "занавеск": "openable.curtain",
    "ворот": "openable",
    "двер": "openable",
    "калитк": "openable",
    "окн": "openable",
    "телевизор": "media_device.tv",
    "тв": "media_device.tv",
    "телек": "media_device.tv",
    "приставк": "media_device.tv_box",
    "ресивер": "media_device.receiver",
    "колонк": "audio.speaker",
    "акустик": "media_device.receiver",
    "динамик": "media_device.receiver",
    "кондиционер": "thermostat.ac",
    "кондей": "thermostat.ac",
    "сплит": "thermostat.ac",
    "термостат": "thermostat",
    "котел": "thermostat",
    "бойлер": "thermostat",
    "обогревател": "thermostat",
    "радиатор": "thermostat",
    "чайник": "cooking.kettle",
    "пылесос": "vacuum_cleaner",
    "выключател": "switch",
    "переключател": "switch",
    "датчик": "sensor",
    "сенсор": "sensor",
}

# --- Датчики: «величина» в запросе -> вид трейта ---------------------------
# Ключи — начала слов (сравниваются через startswith по нормализованному
# токену), поэтому падежи и словоформы («влажность», «влажности», «процентов»)
# попадают в свой вид автоматически. Нужно, чтобы «влажность в бане» возвращала
# влажность, а не температуру того же помещения.
SENSOR_KIND_ROOTS = (
    ("температур", "temperature_sensor"),
    ("градус", "temperature_sensor"),
    ("влажност", "humidity_sensor"),
    ("влаг", "humidity_sensor"),
    ("процент", "humidity_sensor"),
    ("давлен", "pressure_sensor"),
    ("паскал", "pressure_sensor"),
    ("освещен", "illumination_sensor"),
    ("яркост", "illumination_sensor"),
    ("люкс", "illumination_sensor"),
    ("углекисл", "co2_level_sensor"),
    ("co2", "co2_level_sensor"),
    ("напряжен", "voltage_sensor"),
    ("вольт", "voltage_sensor"),
    ("мощност", "power_sensor"),
    ("ватт", "power_sensor"),
    ("батаре", "battery_level_sensor"),
    ("аккумулятор", "battery_level_sensor"),
    ("заряд", "battery_level_sensor"),
    ("pm25", "pm25_sensor"),
    ("пыл", "pm25_sensor"),
    ("дым", "smoke_sensor"),
    ("газ", "gas_sensor"),
    ("протечк", "water_leak_sensor"),
    ("движен", "motion_sensor"),
)

# Человекочитаемые названия величин (для ответа и озвучки).
SENSOR_KIND_TITLES = {
    "temperature_sensor": "температура",
    "humidity_sensor": "влажность",
    "pressure_sensor": "давление",
    "illumination_sensor": "освещённость",
    "co2_level_sensor": "уровень углекислого газа",
    "pm25_sensor": "уровень пыли",
    "voltage_sensor": "напряжение",
    "amperage_sensor": "ток",
    "power_sensor": "мощность",
    "battery_level_sensor": "заряд батареи",
    "water_level_sensor": "уровень воды",
    "water_leak_sensor": "протечка",
    "smoke_sensor": "дым",
    "gas_sensor": "газ",
    "motion_sensor": "движение",
}

# Слова-команды (не участвуют в поиске устройства).
COMMAND_WORDS = {
    "включи", "выключи", "включить", "выключить", "включай", "выключай",
    "зажги", "потуши", "погаси", "сделай", "поставь", "установи", "переключи",
    "открой", "закрой", "приоткрой", "открыть", "закрыть", "приоткрыть",
    "откройся", "закройся", "на", "во", "в", "и", "а", "мне", "нужно",
    "пожалуйста", "давай", "его", "её", "ее", "этот", "эту", "это",
    "какой", "какая", "какое", "какие", "что", "где", "сколько", "который",
    "включен", "включено", "включён", "выключен", "выключено", "работает",
    "работают", "состояние", "статус", "горит", "сейчас", "ли",
}

# «весь / все» — действие на всю группу (тип в комнате); «везде / во всём доме» — весь дом.
SCOPE_ALL_WORDS = {"весь", "все", "всё", "вся", "всю", "всех", "всему", "всем", "полностью", "целиком"}
SCOPE_HOUSE_WORDS = {"везде", "всюду", "дому", "доме", "дом", "дома"}

# Порядок важен: длинные окончания проверяем первыми.
WORD_ENDINGS = (
    "ыми", "ими", "ого", "его", "ому", "ему", "ами", "ями", "ой", "ый", "ий",
    "ая", "яя", "ое", "ее", "ые", "ие", "ов", "ев", "ам", "ям", "ах", "ях",
    "ом", "ем", "а", "я", "о", "е", "у", "ю", "и", "ы", "ь",
)


def normalize_text(value):
    """Приводит текст к сравнимому виду: нижний регистр, ё->е, пунктуация -> пробел."""
    if not isinstance(value, str):
        return ""
    value = value.lower().replace("ё", "е")
    value = re.sub(r"[^\w%]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def stem(word):
    """Отрезает типовое русское окончание (для сопоставления словоформ)."""
    word = (word or "").strip()
    for ending in WORD_ENDINGS:
        if len(word) - len(ending) >= 3 and word.endswith(ending):
            return word[: -len(ending)]
    return word


def extract_sensor_kind(text):
    """Вид измеряемой величины из текста («влажность в бане» -> humidity_sensor).

    Возвращает канонический вид трейта (temperature_sensor/humidity_sensor/…)
    или None, если величина в тексте не названа.
    """
    for token in normalize_text(text).split():
        for root, kind in SENSOR_KIND_ROOTS:
            if token.startswith(root):
                return kind
    return None


def sensor_kind_title(kind):
    """Человекочитаемое название величины («влажность»); "" для неизвестной."""
    return SENSOR_KIND_TITLES.get(normalize_device_type(kind), "")


def tokens_match(a, b):
    """Совпадение слов с учётом словоформ (сравниваем по стемам)."""
    if not a or not b or len(a) < 3 or len(b) < 3:
        return a == b
    return a == b or a.startswith(b) or b.startswith(a)


def room_matches(device_room, wanted_room):
    """Сравнивает комнату устройства с запрошенной (по стемам всех слов)."""
    if not device_room or not wanted_room:
        return False
    dev_tokens = normalize_text(device_room).split()
    wanted_tokens = [stem(t) for t in normalize_text(wanted_room).split() if t]
    if not wanted_tokens:
        return False
    return all(
        any(tokens_match(stem(dt), wt) for dt in dev_tokens)
        for wt in wanted_tokens
    )


def normalize_device_type(value):
    """Приводит тип устройства к каноническому виду (без префикса devices.types.)."""
    if not isinstance(value, str):
        return ""
    value = value.strip().lower()
    if value.startswith(DEVICES_TYPES_PREFIX):
        value = value[len(DEVICES_TYPES_PREFIX):]
    return value


def normalize_mac(value):
    """Приводит идентификатор колонки к каноническому виду (hex, верхний регистр).

    «aa:bb:cc:dd:ee:ff», «AA-BB-CC-DD-EE-FF» и «aabbccddeeff» дают одну и ту же
    строку «AABBCCDDEEFF». Числовой ID устройства XiaoZhi («123456») остаётся
    без изменений. Нужно для сопоставления device_id из XiaoZhi (MAC колонки
    либо её ID в xiaozhi.me) с полем MAC устройства, где хранится один из них.
    """
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^0-9A-Fa-f]", "", value).upper()


def device_type_matches(spec_type, required_types) -> bool:
    """Сравнивает тип устройства со списком требуемых типов.

    Учитывает:
      * префикс ``devices.types.`` (``light`` == ``devices.types.light``);
      * семейства типов (``thermostat`` соответствует ``thermostat.ac``,
        ``media_device`` соответствует ``media_device.tv`` и т.п.).
    """
    spec_norm = normalize_device_type(spec_type)
    if not spec_norm:
        return False
    if isinstance(required_types, str):
        required_types = [required_types]
    for required in required_types or []:
        req_norm = normalize_device_type(required)
        if not req_norm:
            continue
        if spec_norm == req_norm:
            return True
        if spec_norm.startswith(req_norm + ".") or req_norm.startswith(spec_norm + "."):
            return True
        # В запросе часто называют только «хвост» типа: 'speaker' = 'audio.speaker',
        # 'tv' = 'media_device.tv', 'receiver' = 'media_device.receiver'.
        if spec_norm.endswith("." + req_norm):
            return True
    return False


# --- Планировщик: дни недели и время --------------------------------------

# Канонический порядок дней (нумерация как в PHP date('N'): 1=Пн .. 7=Вс).
WEEKDAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_TO_NUM = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 7}

# «слово -> канонический день/токен». Ключи: нижний регистр, ё->е.
_WEEKDAY_ALIASES = {
    "mon": "mon", "monday": "mon", "пн": "mon", "пон": "mon", "понедельник": "mon",
    "tue": "tue", "tues": "tue", "tuesday": "tue", "вт": "tue", "вторник": "tue",
    "wed": "wed", "weds": "wed", "wednesday": "wed", "ср": "wed", "среда": "wed",
    "thu": "thu", "thur": "thu", "thurs": "thu", "thursday": "thu", "чт": "thu",
    "четверг": "thu",
    "fri": "fri", "friday": "fri", "пт": "fri", "пятница": "fri",
    "sat": "sat", "saturday": "sat", "сб": "sat", "суббота": "sat",
    "sun": "sun", "sunday": "sun", "вс": "sun", "воскресенье": "sun",
    # Специальные токены
    "once": "once", "onetime": "once", "разово": "once", "одноразово": "once",
    "одинраз": "once",
    "daily": "daily", "everyday": "daily", "каждыйдень": "daily",
    "ежедневно": "daily", "ежедневный": "daily",
    "weekdays": "weekdays", "weekday": "weekdays", "будни": "weekdays",
    "будний": "weekdays", "будням": "weekdays", "рабочие": "weekdays",
    "weekends": "weekends", "weekend": "weekends", "выходные": "weekends",
    "выходной": "weekends", "выходным": "weekends",
    "all": "daily", "вседни": "daily", "каждый": "daily",
}


def normalize_days(days):
    """Приводит дни к каноническому списку.

    Принимает список или строку (разделители «,», «;», пробел). Понимает
    русские и английские названия (полные/краткие, падежи), а также токены
    once/daily/weekdays/weekends. Результат — либо ``["once"]``, либо
    подмножество ``["mon".."sun"]`` в каноническом порядке. Пустой ввод
    трактуется как «каждый день».
    """
    if days is None:
        return ["once"]
    if isinstance(days, str):
        raw = re.split(r"[,;\s]+", days)
    elif isinstance(days, (list, tuple, set)):
        raw = []
        for item in days:
            if item is None:
                continue
            raw.extend(re.split(r"[,;\s]+", str(item)))
    else:
        raw = [str(days)]

    result = []
    seen = set()

    def add(token):
        if token and token not in seen:
            seen.add(token)
            result.append(token)

    for item in raw:
        key = normalize_text(item)
        if not key:
            continue
        mapped = _WEEKDAY_ALIASES.get(key)
        if mapped is None and len(key) >= 3:
            # «понедельникам», «saturdays» и т.п. — сопоставляем по префиксу.
            for alias, canon in _WEEKDAY_ALIASES.items():
                if len(alias) >= 3 and key.startswith(alias):
                    mapped = canon
                    break
        if mapped is None:
            continue
        if mapped == "once":
            return ["once"]
        if mapped in ("daily",):
            return list(WEEKDAY_ORDER)
        if mapped == "weekdays":
            for day in WEEKDAY_ORDER[:5]:
                add(day)
        elif mapped == "weekends":
            for day in WEEKDAY_ORDER[5:]:
                add(day)
        else:
            add(mapped)

    if not result:
        return list(WEEKDAY_ORDER)
    return [day for day in WEEKDAY_ORDER if day in seen]


def normalize_time(value):
    """Приводит время к 'HH:MM'. Возвращает None, если разобрать не удалось.

    Понимает «17:15», «7:5», «17.15», «17-15», «17 15», «в 17:15».
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    text = re.sub(r"^\s*в\s+", "", text)
    match = re.search(r"(\d{1,2})\s*[:.\-\s]\s*(\d{1,2})", text)
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return f"{hours:02d}:{minutes:02d}"


class MajorDoMoMCP:
    def __init__(self, url: str = "127.0.0.1"):
        self.MAJORDOMO_URL = os.getenv("MAJORDOMO_URL", f"http://{url}")
        logger.info(f"url: {self.MAJORDOMO_URL}")
        self.config = {}
        self.db_conn = None
        self.config = self.load_config() or {}
        self.mcp_endpoint = (self.config.get('MCP_ENDPOINT'))
        self.debug = (self.config.get('LOG_DEBMES', '0') == '1')
        self.debug_ping = (self.config.get('LOG_PING', '0') == '1')
        self.openable_states = {}  # Словарь для хранения состояний openable устройств
        # Антидребезг команд: {device_key: (signature, monotonic_time)}.
        # Защищает устройства от зацикливания ассистента (повтор одной и той же
        # команды много раз подряд).
        self._recent_commands = {}
        self._aliases_cache = None
        self._aliases_cache_time = 0.0
        self._devices_cache = None
        self._devices_cache_time = 0.0
        self._devices_has_mac = None  # Есть ли колонка MAC в mcp_devices (определяется лениво)
        # Индекс MAC -> комната (строится в load_devices_from_db). Нужен для
        # колонок XiaoZhi: у них нет управляющих trait'ов, поэтому в список
        # устройств они не попадают, но их комнату надо знать по MAC.
        self._rooms_by_mac = {}
        self._current_room = ""  # Комната колонки-источника (по MAC) для TTS-ответов
        self._rooms_cache = None
        self._rooms_cache_time = 0.0
        self._scenes_cache = None
        self._scenes_cache_time = 0.0
        # Поведение при запросе яркости для не-диммируемого устройства:
        # 'error' — сообщить об ошибке; 'on' — включить на 100% с предупреждением.
        self.brightness_no_dimmer = (self.config.get('BRIGHTNESS_NO_DIMMER') or 'error')
        logger.info(f"debug: {self.debug}")
        
    def load_config(self):
        """Загружает конфигурацию из таблицы project_modules"""
        sql_query = f"SELECT * FROM project_modules WHERE NAME = 'mcp'"
        conn = self.get_db_connection()
        if not conn:
            return {}  # Возвращаем пустой словарь, если нет соединения
        
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql_query)
                rec_tuple = cursor.fetchone()
                
                if rec_tuple:
                    columns = [desc[0] for desc in cursor.description]
                    rec = {}
                    for i, col in enumerate(columns):
                        if i < len(rec_tuple):
                            rec[col] = rec_tuple[i]
                    
                    if rec and rec.get("DATA"):
                        import pickle
                        try:
                            import phpserialize
                            data = phpserialize.loads(rec["DATA"].encode('latin1'))
                            # Декодируем все ключи и значения
                            config = {}
                            for k, v in data.items():
                                if isinstance(k, bytes):
                                    k = k.decode('utf8')
                                if isinstance(v, bytes):
                                    v = v.decode('utf8')
                                config[k] = v
                            return config
                        except:
                            return {"DATA": rec["DATA"]}
            return {}  # Возвращаем пустой словарь, если не нашли данных
        except Exception as e:
            self.debmes(f"Ошибка загрузки конфигурации: {e}", "mcp")
            logger.error(f"Ошибка загрузки конфигурации: {e}")
            return {}  # Возвращаем пустой словарь в случае ошибки
        finally:
            conn.close()

    def debmes(self, error_message, log_level="debug"):
        """Аналог PHP функции DebMes — логирование в датированные папки с поддержкой вложенных уровней."""
        # Проверяем отключение логирования
        if os.getenv('SETTINGS_SYSTEM_DISABLE_DEBMES', '0') == '1':
            return

        # Определяем путь к корню логов
        if os.getenv('SETTINGS_SYSTEM_DEBMES_PATH'):
            base_path = os.getenv('SETTINGS_SYSTEM_DEBMES_PATH')
        elif os.getenv('LOG_DIRECTORY'):
            base_path = os.getenv('LOG_DIRECTORY')
        else:
            base_path = "/var/www/html/cms/debmes"

        # Максимальный размер лога (по умолчанию 5 МБ)
        max_log_size = int(os.getenv('LOG_MAX_SIZE', 5)) * 1024 * 1024

        # Создаём базовую директорию
        os.makedirs(base_path, exist_ok=True)

        # Формируем путь: base_path/YYYY-MM-DD/[level_parts...]
        date_str = datetime.now().strftime('%Y-%m-%d')
        current_path = os.path.join(base_path, date_str)

        # Обрабатываем log_level как в PHP: разбиваем по '/'
        level_parts = log_level.strip().split('/')
        total = len(level_parts)

        # Создаём вложенные директории (кроме последней части — это имя файла без расширения)
        for i in range(total):
            part = level_parts[i]
            if i < total - 1:  # не последний элемент — это папка
                current_path = os.path.join(current_path, part)
                os.makedirs(current_path, exist_ok=True)
            else:  # последний элемент — имя файла (без .log)
                file_name = part + '.log'
                log_file = os.path.join(current_path, file_name)

        # Проверяем размер файла перед записью
        if os.path.exists(log_file) and os.path.getsize(log_file) > max_log_size:
            return

        # Преобразуем сообщение в строку (как в PHP)
        if isinstance(error_message, (dict, list)):
            error_message = json.dumps(error_message, ensure_ascii=False, indent=2)
        elif hasattr(error_message, '__dict__'):
            error_message = json.dumps(error_message.__dict__, ensure_ascii=False, indent=2)
        else:
            error_message = str(error_message)

        # Формат времени: HH:MM:SS.microseconds (3 цифры, как в microtime() * 1000 → миллисекунды)
        now = datetime.now()
        fractional_seconds = now.microsecond / 1_000_000
        timestamp = f"{now.strftime('%H:%M:%S')} {fractional_seconds:.8f}"

        # Запись в файл
        try:
            with open(log_file, "a+", encoding='utf-8') as f:
                f.write(f"{timestamp} {error_message}\n")
        except Exception as e:
            # Резервный логгер
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"debmes failed to write to '{log_file}': {e}")

    def get_db_connection(self):
        """Создает соединение с базой данных MajorDoMo, используя параметры из config.php"""
        try:
            import pymysql

            # Ищем config.php в предсказуемых местах и через env-переопределение.
            config_candidates = []
            env_config = os.getenv("MAJORDOMO_CONFIG")
            if env_config:
                config_candidates.append(env_config)

            module_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            config_candidates.append(os.path.join(module_root, "config.php"))
            config_candidates.append("/var/www/html/config.php")
            config_candidates.append("C:/_majordomo/config.php")

            config_path = None
            for candidate in config_candidates:
                if candidate and os.path.exists(candidate):
                    config_path = candidate
                    break

            if not config_path:
                raise Exception(f"Файл конфигурации не найден. Проверены пути: {config_candidates}")
            
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Извлекаем параметры подключения с помощью регулярных выражений
            import re
            db_host_match = re.search(r"Define\('DB_HOST',\s*'([^']+)'\)", content)
            db_name_match = re.search(r"Define\('DB_NAME',\s*'([^']+)'\)", content)
            db_user_match = re.search(r"Define\('DB_USER',\s*'([^']+)'\)", content)
            db_password_match = re.search(r"Define\('DB_PASSWORD',\s*'([^']+)'\)", content)
            
            if not all([db_host_match, db_name_match, db_user_match, db_password_match]):
                raise Exception("Не найдены параметры подключения к БД в config.php")
            
            db_host = db_host_match.group(1)
            db_name = db_name_match.group(1)
            db_user = db_user_match.group(1)
            db_password = db_password_match.group(1)
            
            conn = pymysql.connect(
                host=db_host,
                user=db_user,
                password=db_password,
                database=db_name,
                charset='utf8mb4'
            )
            return conn
        except Exception as e:
            logger.error(f"Ошибка подключения к БД: {e}")
            return None

    def load_devices_from_db(self, force: bool = False):
        """Загружает устройства из mcp_devices (одна запись = одно устройство).

        Спецификация: ``{id, title, aliases, type, category, room, object,
        property, traits, config}``.

        Устройства без связанного объекта/свойства MajorDoMo пропускаются:
        управлять нечем, а подставлять Название в качестве объекта нельзя —
        Название (TITLE) является только информационным полем и не участвует
        в распознавании.

        Результат кэшируется на ALIASES_CACHE_TTL секунд: за один запрос к LLM
        функцию вызывают несколько раз (поиск устройства + список доступных),
        и без кэша это давало столько же подключений к БД.
        """
        if not force and self._devices_cache is not None:
            if (time.time() - self._devices_cache_time) < ALIASES_CACHE_TTL:
                return self._devices_cache

        conn = self.get_db_connection()
        if not conn:
            logger.error("Не удалось подключиться к БД для загрузки устройств")
            return []

        devices = []
        rooms_by_mac = {}
        try:
            with conn.cursor() as cursor:
                has_mac = self._ensure_device_mac_column(cursor)
                mac_field = ", MAC" if has_mac else ""
                cursor.execute(
                    "SELECT ID, TITLE, ALIACE, TYPE, ROOM, TRAITS, CONFIG" + mac_field + " FROM mcp_devices"
                )
                rows = cursor.fetchall()

            for row in rows:
                dev_id, title, aliace, dev_type, room, traits_json, config_json = row[:7]
                mac = row[7] if has_mac and len(row) > 7 else ''
                traits = safe_json_loads(traits_json, {})
                config = safe_json_loads(config_json, {})
                if not room and isinstance(config, dict):
                    room = config.get('room') or ''

                # MAC -> комната. Запоминаем до проверки trait'ов: у колонки
                # XiaoZhi управляющих trait'ов нет (linked_object/property пустые),
                # поэтому ниже она отбрасывается из списка устройств, но по MAC
                # нужно понимать, из какой комнаты пришла голосовая команда.
                mac_norm = normalize_mac(mac)
                if mac_norm:
                    rooms_by_mac[mac_norm] = room or ''

                linked_object, linked_property = first_linked_trait(traits)
                dev_type = dev_type or 'unknown'
                is_speaker = device_type_matches(dev_type, ROOM_BINDING_TYPES)

                if (not linked_object or not linked_property) and not is_speaker:
                    logger.info(f"Устройство '{title}' без связанного объекта/свойства — пропущено")
                    continue

                devices.append({
                    "id": dev_id,
                    "title": title or '',
                    "aliases": parse_aliases(aliace),
                    "type": dev_type,
                    "category": dev_type.split('.')[-1] if '.' in dev_type else dev_type,
                    "room": room or '',
                    "mac": mac or '',
                    "object": linked_object or '',
                    "property": linked_property or '',
                    "traits": traits,
                    "config": config if isinstance(config, dict) else {},
                })
        except Exception as e:
            logger.error(f"Ошибка загрузки устройств из БД: {e}")
            return []
        finally:
            conn.close()

        self._rooms_by_mac = rooms_by_mac
        self._devices_cache = devices
        self._devices_cache_time = time.time()
        return devices

    def _ensure_device_mac_column(self, cursor):
        """Проверяет наличие колонки MAC в mcp_devices и добавляет её при необходимости.

        Нужно для уже установленных модулей: колонка MAC появилась позже, а
        переустановка таблицы устройств нежелательна.
        """
        if self._devices_has_mac is not None:
            return self._devices_has_mac

        try:
            cursor.execute("SHOW COLUMNS FROM mcp_devices")
            columns = {str(row[0]).upper() for row in cursor.fetchall()}
        except Exception as e:
            logger.warning(f"Не удалось прочитать схему mcp_devices: {e}")
            columns = set()

        if 'MAC' not in columns:
            try:
                cursor.execute("ALTER TABLE mcp_devices ADD COLUMN MAC varchar(64) NOT NULL DEFAULT ''")
                columns.add('MAC')
            except Exception as e:
                logger.warning(f"Не удалось добавить колонку MAC в mcp_devices: {e}")

        self._devices_has_mac = 'MAC' in columns
        return self._devices_has_mac

    @staticmethod
    def device_to_spec(device):
        """Преобразует запись устройства в спецификацию, ожидаемую тулзами."""
        return {
            "id": device.get("id"),
            "object": device.get("object"),
            "property": device.get("property"),
            "category": device.get("category"),
            "type": device.get("type"),
            "traits": device.get("traits"),
            "config": device.get("config"),
            "room": device.get("room"),
            "title": device.get("title"),
            "aliases": device.get("aliases"),
            "mac": device.get("mac"),
        }

    @staticmethod
    def is_device_reversed(spec):
        """Флаг «Реверс» устройства.

        Если в CONFIG устройства (capabilities[].parameters) выставлен
        reverse=true, то инвертируются смыслы «вкл/выкл» и «открыть/закрыть».
        Для совместимости принимается и старый ключ split.
        """
        config = (spec or {}).get("config")
        if not isinstance(config, dict):
            return False
        for capability in config.get("capabilities") or []:
            parameters = capability.get("parameters") or {}
            if parameters.get("reverse") or parameters.get("split"):
                return True
        return False

    def load_aliases_from_db(self, type: str = None, force: bool = False):
        """Индекс «имя -> спецификации устройств» (обратная совместимость).

        type: если задан, устройства этого типа исключаются (историческое поведение).
        """
        aliases = {}
        for device in self.load_devices_from_db(force=force):
            if type and normalize_device_type(device.get("type")) == normalize_device_type(type):
                continue
            spec = self.device_to_spec(device)
            for name in device.get("aliases") or []:
                if name:
                    aliases.setdefault(name, []).append(spec)
        return aliases

    def load_rooms_from_db(self, force: bool = False):
        """Список комнат: TITLE из таблицы locations (fallback — комнаты устройств)."""
        if not force and self._rooms_cache is not None:
            if (time.time() - self._rooms_cache_time) < ROOMS_CACHE_TTL:
                return self._rooms_cache

        rooms = []
        conn = self.get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT TITLE FROM locations ORDER BY TITLE")
                    rooms = [row[0] for row in cursor.fetchall() if row[0]]
            except Exception as e:
                logger.error(f"Ошибка загрузки комнат: {e}")
            finally:
                conn.close()

        if not rooms:
            rooms = sorted({d.get("room") for d in self.load_devices_from_db() if d.get("room")})

        self._rooms_cache = rooms
        self._rooms_cache_time = time.time()
        return rooms

    # === РАСПОЗНАВАНИЕ: ТИП / КОМНАТА / МАСШТАБ ===

    @staticmethod
    def extract_device_type(text):
        """Тип устройства из типового слова («свет» -> light). None, если не найден."""
        for token in normalize_text(text).split():
            found = TYPE_WORDS.get(stem(token))
            if found:
                return found
        return None

    def extract_room(self, text, rooms=None):
        """Комната из запроса («включи свет в зале» -> «Зал»).

        Сравнение пословное, с учётом словоформ. При неоднозначности
        (несколько равнозначных комнат) возвращает None.
        """
        rooms = rooms if rooms is not None else self.load_rooms_from_db()
        tokens = [stem(t) for t in normalize_text(text).split()]
        if not tokens:
            return None

        matches = []
        for room in rooms:
            parts = [stem(p) for p in normalize_text(room).split() if p]
            if not parts:
                continue
            if all(any(tokens_match(token, part) for token in tokens) for part in parts):
                matches.append(room)

        if not matches:
            return None
        matches.sort(key=lambda value: -len(value))
        if len(matches) > 1 and len(matches[0]) == len(matches[1]):
            return None
        return matches[0]

    @staticmethod
    def extract_scope(text, room=None):
        """Масштаб действия: (scope, ask_room).

        scope: None | 'all_in_room' | 'all_house'.
        ask_room=True — в запросе есть «весь/все», но комната не названа.
        """
        tokens = set(normalize_text(text).split())
        if not tokens:
            return None, False
        has_all = bool(tokens & SCOPE_ALL_WORDS)
        has_house = bool(tokens & SCOPE_HOUSE_WORDS)
        if has_house:
            return 'all_house', False
        if has_all:
            if room:
                return 'all_in_room', False
            return None, True
        return None, False

    @staticmethod
    def parse_percent(text):
        """Процент из фразы («на 30%», «30 процентов», «яркость 40») или None."""
        normalized = normalize_text(text)
        match = re.search(r'(\d{1,3})\s*(?:%|процент\w*)', normalized)
        if not match:
            match = re.search(r'(?:яркост\w*|на|до)\s+(\d{1,3})\b', normalized)
        if not match:
            return None
        value = int(match.group(1))
        if value < 0 or value > 100:
            return None
        return value

    def _query_remainder(self, text, room=None, device_type=None):
        """Остаток фразы без команд, типа и комнаты (для поиска по псевдониму/синониму устройства)."""
        drop_stems = {stem(word) for word in COMMAND_WORDS}
        # В остаток не входят ЛЮБЫЕ типовые слова («свет», «шторы», «окно», …):
        # они задают тип устройства, а не псевдоним. Если выкидывать только
        # слова распознанного типа, результат зависит от порядка слов:
        # «свет над окном» работало, а «над окном свет» — нет (слово «окно»
        # ошибочно подсказывало тип openable, и «свет» оставался в остатке).
        drop_stems.update(stem(token) for token in normalize_text(text).split() if stem(token) in TYPE_WORDS)

        if room:
            drop_stems.update(stem(part) for part in normalize_text(room).split())

        remainder = []
        for token in normalize_text(text).split():
            if stem(token) in drop_stems:
                continue
            if re.fullmatch(r'\d{1,3}%?', token):
                continue
            if token in SCOPE_ALL_WORDS or token in SCOPE_HOUSE_WORDS:
                continue
            remainder.append(token)
        return remainder

    @staticmethod
    def match_alias_names(aliases, remainder):
        """Подбирает ключи алиасов по остатку фразы.

        Возвращает список подходящих ключей: [] — ничего не нашли, [key] —
        однозначно, [k1, k2] — неоднозначно (нужно уточнение устройства).
        """
        if not remainder:
            return []
        joined = ' '.join(remainder)
        if joined in aliases:
            return [joined]

        candidates = set()
        for key in aliases:
            key_tokens = [stem(t) for t in normalize_text(key).split() if t]
            if not key_tokens:
                continue
            if all(any(tokens_match(token, key_token) for key_token in key_tokens) for token in remainder):
                candidates.add(key)
        return sorted(candidates)

    def resolve_devices(self, query="", device_type=None, room=None, scope=None, default_room=None):
        """Единый резолвер: фраза -> устройства либо запрос на уточнение.

        Возвращает словарь со status:
          ok          — devices (список спецификаций), найден однозначный набор;
          need_room   — rooms (комнаты, где есть подходящие устройства);
          need_device — candidates (варианты внутри комнаты);
          not_found   — available (что вообще есть по фильтру).

        default_room — комната по умолчанию (например, комната колонки, от
        которой пришла команда). Применяется, только если комната не задана
        явно и не названа в запросе.
        """
        devices = self.load_devices_from_db()
        rooms = self.load_rooms_from_db()
        text = normalize_text(query)

        if not room:
            room = self.extract_room(text, rooms)
        if not room and default_room:
            room = default_room

        type_inferred = False
        if not device_type:
            device_type = self.extract_device_type(text)
            type_inferred = bool(device_type)
        if isinstance(device_type, str):
            device_type = [device_type] if device_type else None

        detected_scope, ask_room = self.extract_scope(text, room)
        if not scope:
            scope = detected_scope
        if scope == 'all_room':
            scope = 'all_in_room'
        if scope == 'all_house':
            ask_room = False

        def type_ok(device):
            return (not device_type) or device_type_matches(device.get("type"), device_type)

        def room_ok(device):
            return (not room) or room_matches(device.get("room"), room)

        def public(device):
            spec = self.device_to_spec(device)
            return {
                "id": spec.get("id"),
                "title": spec.get("title"),
                "room": spec.get("room"),
                "type": spec.get("type"),
                "aliases": spec.get("aliases"),
                "object": spec.get("object"),
                "property": spec.get("property"),
                "_spec": spec,
            }

        def rooms_for(devs):
            return sorted({d.get("room") for d in devs if d.get("room")})

        def describe(devs):
            return [
                {"title": d.get("title"), "room": d.get("room"), "type": d.get("type")}
                for d in devs
            ]

        if scope == 'all_house':
            candidates = [d for d in devices if type_ok(d)]
            if not candidates:
                return {"status": "not_found", "type": device_type, "room": room,
                        "available": describe([d for d in devices if type_ok(d)]) or describe(devices)}
            return {"status": "ok", "scope": "all_house", "type": device_type, "room": room,
                    "devices": [public(d) for d in candidates]}

        if scope == 'all_in_room':
            if not room:
                pool = [d for d in devices if type_ok(d)]
                return {"status": "need_room", "type": device_type, "scope": "all_in_room",
                        "rooms": rooms_for(pool), "candidates": describe(pool)}
            candidates = [d for d in devices if type_ok(d) and room_ok(d)]
            if not candidates:
                return {"status": "not_found", "type": device_type, "room": room,
                        "available": describe([d for d in devices if type_ok(d)]) or describe(devices)}
            return {"status": "ok", "scope": "all_in_room", "type": device_type, "room": room,
                    "devices": [public(d) for d in candidates]}

        if ask_room:
            pool = [d for d in devices if type_ok(d)]
            if len(rooms_for(pool)) <= 1:
                # Комната и так одна — спрашиваем конкретное устройство.
                return {"status": "need_device", "type": device_type,
                        "room": rooms_for(pool)[0] if rooms_for(pool) else room,
                        "candidates": describe(pool)}
            return {"status": "need_room", "type": device_type, "rooms": rooms_for(pool),
                    "candidates": describe(pool)}

        candidates = [d for d in devices if type_ok(d) and room_ok(d)]

        aliases = self.load_aliases_from_db()
        remainder = self._query_remainder(text, room=room, device_type=device_type)
        matched_keys = self.match_alias_names(aliases, remainder)
        if matched_keys:
            ids = {spec.get("id") for key in matched_keys for spec in aliases[key]}
            narrowed = [d for d in candidates if d.get("id") in ids]
            if not narrowed and type_inferred:
                # Тип распознан эвристически по слову внутри псевдонима устройства
                # (например, «свет над окном» → «над окном» ошибочно даёт тип
                # openable из-за слова «окно»). Явное совпадение по псевдониму
                # устройства важнее такой эвристики.
                narrowed = [d for d in devices if d.get("id") in ids and room_ok(d)]
            if narrowed:
                candidates = narrowed
        elif remainder:
            # В запросе есть нераспознанное слово — скорее всего ошиблись именем.
            return {"status": "not_found", "type": device_type, "room": room,
                    "unrecognized": remainder,
                    "available": describe([d for d in devices if type_ok(d)]) or describe(devices)}

        if not candidates:
            return {"status": "not_found", "type": device_type, "room": room,
                    "available": describe([d for d in devices if type_ok(d)]) or describe(devices)}

        if len(candidates) == 1:
            return {"status": "ok", "scope": "one", "type": device_type, "room": room,
                    "devices": [public(candidates[0])]}

        candidate_rooms = rooms_for(candidates)
        if not room and len(candidate_rooms) > 1:
            return {"status": "need_room", "type": device_type, "rooms": candidate_rooms,
                    "candidates": describe(candidates)}
        return {"status": "need_device", "type": device_type,
                "room": room or (candidate_rooms[0] if len(candidate_rooms) == 1 else None),
                "candidates": describe(candidates)}

    # === СЦЕНЫ И ГРУППЫ ===

    def load_scenes_from_db(self, force: bool = False):
        """Загружает сцены из mcp_scenes. Таблица создаётся при необходимости."""
        if not force and self._scenes_cache is not None:
            if (time.time() - self._scenes_cache_time) < ALIASES_CACHE_TTL:
                return self._scenes_cache

        conn = self.get_db_connection()
        if not conn:
            logger.error("Не удалось подключиться к БД для загрузки сцен")
            return []

        devices_by_id = {d.get("id"): d for d in self.load_devices_from_db()}
        scenes = []
        try:
            with conn.cursor() as cursor:
                cursor.execute(SCENES_TABLE_DDL + table_engine_suffix(cursor))
                cursor.execute("SELECT ID, TITLE, ALIACE, ENABLED, ACTIONS FROM mcp_scenes ORDER BY ID")
                rows = cursor.fetchall()
            conn.commit()

            for scene_id, title, aliace, enabled, actions_json in rows:
                actions_raw = safe_json_loads(actions_json, [])
                actions = []
                for action in actions_raw if isinstance(actions_raw, list) else []:
                    if not isinstance(action, dict):
                        continue
                    device = devices_by_id.get(action.get("device_id"))
                    actions.append({
                        "device_id": action.get("device_id"),
                        "action": action.get("action") or "on",
                        "brightness": action.get("brightness"),
                        "device": self.device_to_spec(device) if device else None,
                    })
                scenes.append({
                    "id": scene_id,
                    "title": title or '',
                    "aliases": parse_aliases(aliace),
                    "enabled": bool(enabled),
                    "actions": actions,
                })
        except Exception as e:
            logger.error(f"Ошибка загрузки сцен из БД: {e}")
            return []
        finally:
            conn.close()

        self._scenes_cache = scenes
        self._scenes_cache_time = time.time()
        return scenes

    def find_scene(self, scene_query):
        """Находит сцену по синониму. Возвращает (scene, ambiguous)."""
        scenes = self.load_scenes_from_db()
        aliases = {}
        for scene in scenes:
            for name in scene.get("aliases") or []:
                aliases.setdefault(name, []).append(scene)

        normalized = normalize_text(scene_query)
        if normalized in aliases:
            found = aliases[normalized]
            return (found[0], False) if len(found) == 1 else (None, True)

        candidates = set()
        query_tokens = [stem(t) for t in normalized.split() if t]
        for key in aliases:
            key_tokens = [stem(t) for t in normalize_text(key).split() if t]
            if not key_tokens or not query_tokens:
                continue
            if all(any(tokens_match(token, key_token) for key_token in key_tokens) for token in query_tokens):
                for scene in aliases[key]:
                    candidates.add(scene["id"])
        if len(candidates) == 1:
            return next(s for s in scenes if s["id"] == next(iter(candidates))), False
        if len(candidates) > 1:
            return None, True
        return None, False

    # --- CRUD сцен/групп (для голосового управления) ---

    def ensure_scenes_table(self, conn):
        """Создаёт таблицу сцен, если её ещё нет."""
        try:
            with conn.cursor() as cursor:
                cursor.execute(SCENES_TABLE_DDL + table_engine_suffix(cursor))
            conn.commit()
        except Exception as e:
            logger.error(f"Не удалось подготовить таблицу mcp_scenes: {e}")

    def _invalidate_scenes_cache(self):
        """Сбрасывает кэш сцен, чтобы правки были видны сразу же."""
        self._scenes_cache = None
        self._scenes_cache_time = 0.0

    @staticmethod
    def split_aliases_raw(aliace):
        """Разбивает строку ALIACE на исходные (неноpмализованные) части."""
        if not aliace:
            return []
        return [part.strip() for part in re.split(r'[,;]', str(aliace)) if part.strip()]

    @staticmethod
    def format_scene_aliases(names):
        """Собирает ALIACE: trim + дедупликация по нормализованному виду."""
        result = []
        seen = set()
        for name in names or []:
            raw = str(name or '').strip()
            key = normalize_text(raw)
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(raw)
        return ", ".join(result)

    def merge_scene_aliases(self, existing_raw, names):
        """Добавляет новые фразы к существующим алиасам сцены (с дедупликацией)."""
        return self.format_scene_aliases(self.split_aliases_raw(existing_raw) + list(names or []))

    def load_scene_row(self, scene_id):
        """Возвращает «сырую» запись сцены (ALIACE/ACTIONS как в БД)."""
        conn = self.get_db_connection()
        if not conn:
            return None
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT ID, TITLE, ALIACE, ENABLED, ACTIONS FROM mcp_scenes WHERE ID=%s",
                    (int(scene_id),))
                row = cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "title": row[1] or '',
                "aliase": row[2] or '',
                "enabled": bool(row[3]),
                "actions_json": row[4] or '[]',
                "actions": safe_json_loads(row[4], []),
            }
        except Exception as e:
            logger.error(f"Ошибка чтения сцены {scene_id}: {e}")
            return None
        finally:
            conn.close()

    def create_scene(self, title, aliases=None, enabled=True, actions=None):
        """Создаёт сцену. Возвращает ID или None."""
        conn = self.get_db_connection()
        if not conn:
            return None
        try:
            self.ensure_scenes_table(conn)
            title = str(title or '').strip()
            aliace = self.format_scene_aliases(list(aliases or []) or [title])
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO mcp_scenes (TITLE, ALIACE, ENABLED, ACTIONS) VALUES (%s, %s, %s, %s)",
                    (title, aliace, 1 if enabled else 0,
                     json.dumps(actions or [], ensure_ascii=False)))
                scene_id = cursor.lastrowid
            conn.commit()
            self._invalidate_scenes_cache()
            return scene_id
        except Exception as e:
            logger.error(f"Ошибка создания сцены: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return None
        finally:
            conn.close()

    def save_scene(self, scene_id, title, aliace, enabled, actions):
        """Обновляет поля существующей сцены."""
        conn = self.get_db_connection()
        if not conn:
            return False
        try:
            self.ensure_scenes_table(conn)
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE mcp_scenes SET TITLE=%s, ALIACE=%s, ENABLED=%s, ACTIONS=%s WHERE ID=%s",
                    (str(title or ''), str(aliace or ''), 1 if enabled else 0,
                     json.dumps(actions or [], ensure_ascii=False), int(scene_id)))
            conn.commit()
            self._invalidate_scenes_cache()
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения сцены {scene_id}: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            conn.close()

    def delete_scene(self, scene_id):
        """Удаляет сцену по ID."""
        conn = self.get_db_connection()
        if not conn:
            return False
        try:
            self.ensure_scenes_table(conn)
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM mcp_scenes WHERE ID=%s", (int(scene_id),))
            conn.commit()
            self._invalidate_scenes_cache()
            return True
        except Exception as e:
            logger.error(f"Ошибка удаления сцены {scene_id}: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            conn.close()

    def command_recently_sent(self, device_key, signature):
        """Антидребезг: True, если ровно такую же команду этому устройству уже
        отправляли только что (в пределах COMMAND_DEBOUNCE_SEC секунд).

        Защита от зацикливания ассистента: если модель повторяет одну и ту же
        команду подряд, реально на устройство уйдёт только первая. Как только
        приходит другая команда (или команда с другим значением/процентом),
        она отправляется как обычно — это не мешает нормальному управлению.

        Окно настраивается параметром модуля COMMAND_DEBOUNCE_SEC (по умолчанию 30с;
        0 или отрицательное значение полностью отключает антидребезг).
        """
        import time
        try:
            window = float(self.config.get('COMMAND_DEBOUNCE_SEC', '30'))
        except (TypeError, ValueError):
            window = 30.0
        if window <= 0:
            return False
        now = time.monotonic()
        last = self._recent_commands.get(device_key)
        if last is not None and last[0] == signature and (now - last[1]) < window:
            return True
        self._recent_commands[device_key] = (signature, now)
        return False

    def set_device_action(self, spec, action, brightness=None):
        """Выполняет действие над устройством (вкл/выкл, при необходимости яркость)."""
        action = (action or '').strip().lower()

        brightness_trait = find_linked_trait(spec.get("traits"), ["brightness"])
        brightness_warning = None
        is_off = action in ("off", "выключи", "выключить", "0", "нет", "потуши", "погаси", "деактивируй")
        if is_off:
            brightness = None  # выключение яркость не трогает
        elif brightness is not None:
            if not brightness_trait:
                if self.brightness_no_dimmer == 'on':
                    brightness_warning = "brightness_unsupported"
                    brightness = None  # включим как обычно, о предупреждении скажет ответ
                else:
                    return {"success": False, "error": "brightness_unsupported",
                            "target": spec.get("title")}

        if is_off or (brightness is not None and brightness == 0):
            state = ("0", "выключено")
        elif action in ("on", "включи", "включить", "1", "да", "зажги", "активируй") or brightness is not None:
            state = ("1", "включено")
        else:
            return {"success": False, "error": f"unknown_action: {action}"}

        # «Реверс»: у устройства обратная полярность — инвертируем техническое
        # значение (0/1), а пользователю состояние показываем как есть.
        reversed_device = self.is_device_reversed(spec)
        on_value = "0" if reversed_device else "1"
        send_value = on_value if state[0] == "1" else ("1" if on_value == "0" else "0")

        # Антидребезг: если ровно ту же команду этому устройству только что
        # отправляли — не отправляем повторно (защита от зацикливания ассистента).
        _signature = [(str(spec['object']), str(spec['property']), str(send_value))]
        if brightness is not None and brightness_trait:
            _signature.append((str(brightness_trait['object']), str(brightness_trait['property']), str(brightness)))
        _device_key = f"{spec['object']}.{spec['property']}"
        if self.command_recently_sent(_device_key, tuple(_signature)):
            logger.info(f"Антидребезг: повторная команда для '{_device_key}' пропущена")
            result = {"success": True, "target": spec.get("title"), "room": spec.get("room"),
                      "state": state[1], "brightness": brightness, "duplicate": True, "applied": []}
            if brightness_warning:
                result["warning"] = brightness_warning
            return result

        results = []
        resp = self.call_majordomo(
            "POST", f"data/{spec['object']}.{spec['property']}", data={"data": send_value}
        )
        if not resp or resp.status_code != 200:
            return {"success": False, "target": spec.get("title"),
                    "error": f"MajorDoMo error: {resp.status_code if resp else 'timeout'}"}
        results.append(f"{spec['object']}.{spec['property']}={send_value}")

        if brightness is not None and brightness_trait:
            level = str(brightness)
            level_resp = self.call_majordomo(
                "POST", f"data/{brightness_trait['object']}.{brightness_trait['property']}",
                data={"data": level},
            )
            if not level_resp or level_resp.status_code != 200:
                return {"success": False, "target": spec.get("title"),
                        "error": f"MajorDoMo error (brightness): "
                                 f"{level_resp.status_code if level_resp else 'timeout'}"}
            results.append(f"{brightness_trait['object']}.{brightness_trait['property']}={level}")
            if brightness > 0 and state[0] != "1":
                on_trait = find_linked_trait(spec.get("traits"), ["on"]) or {
                    "object": spec["object"], "property": spec["property"]
                }
                self.call_majordomo(
                    "POST", f"data/{on_trait['object']}.{on_trait['property']}", data={"data": on_value}
                )

        result = {"success": True, "target": spec.get("title"), "room": spec.get("room"),
                  "state": state[1], "brightness": brightness, "applied": results}
        if brightness_warning:
            result["warning"] = brightness_warning
        return result

    def call_majordomo(self, method: str, path: str, data=None, params=None):
        """MajorDoMo API (с поддержкой params)"""
        url = f"{self.MAJORDOMO_URL}/api/{path}"
        try:
            if method == "POST":
                if isinstance(data, dict):
                    resp = requests.post(url, json=data, params=params, timeout=15)
                else:
                    resp = requests.post(url, data=data, params=params, timeout=15)
            else:
                resp = requests.get(url, params=params, timeout=15)
            return resp
        except Exception as e:
            logger.error(f"Majordomo API error: {e}")
            return None

    def normalize_query(self, query: str) -> str:
        """Нормализация запросов"""
        query = query.lower().strip()
        # Убираем вводные вопросительные слова, иначе шаблоны ниже (с якорем ^)
        # не срабатывают, например "какая температура в бане".
        query = re.sub(r'^(какая|какой|какое|какие|сколько|что|где)\s+', '', query)
        # Проверяем, содержит ли запрос информацию о температуре
        # (учитываем словоформы: температура/температуру/градусов)
        if 'температур' in query or 'градус' in query:
            # Убираем только само слово и частицы 'в' или 'на'
            patterns = [
                r'^(температур\w*|градус\w*)\s+(в|на)\s+',
                r'^(температур\w*|градус\w*)\s*'
            ]
            for pat in patterns:
                query = re.sub(pat, '', query)
        # Проверяем, содержит ли запрос информацию о включении/выключении света
        elif any(word in query for word in ['включи', 'выключи', 'включить', 'выключить']):
            # Убираем команды включения/выключения, слова 'свет', 'люстра', 'лампа' и частицы 'в' или 'на'
            patterns = [
                r'^(включи|выключи|включить|выключить)\s+(свет|люстру|лампу)\s+(в|на)\s+',
                r'^(включи|выключи|включить|выключить)\s+(свет|люстру|лампу)\s*',
                r'^(свет|люстру|лампу)\s+(в|на)\s+',
                r'^(в|на)\s+'
            ]
            for pat in patterns:
                query = re.sub(pat, '', query)
        # Проверяем, содержит ли запрос информацию об управлении воротами, шторами, жалюзи
        elif any(word in query for word in ['открой', 'приоткрой', 'закрой', 'открыть', 'приоткрыть', 'закрыть']):
            # Убираем команды управления, слова 'ворота', 'шторы', 'жалюзи' и частицы 'в' или 'на'
            patterns = [
                r'^(открой|приоткрой|закрой|открыть|приоткрыть|закрыть)\s+(ворота|дверь|двери|штора|шторы|жалюзи)\s+(в|на)\s+',
                r'^(открой|приоткрой|закрой|открыть|приоткрыть|закрыть)\s+(ворота|дверь|двери|штора|шторы|жалюзи)\s*',
                r'^(ворота|дверь|двери|штора|шторы|жалюзи)\s+(в|на)\s+',
                r'^(в|на)\s+'
            ]
            for pat in patterns:
                query = re.sub(pat, '', query)
        else:
            # Для других случаев применяем общие шаблоны
            patterns = [
                r'^(свет|освещение|статус)\s+(на|в)\s+',
                r'^(температура|влажность|давление)\s+(в|на)\s+',
                r'^(свет|освещение|статус|температура|влажность|давление)\s*',
                r'^(на|в)\s+'
            ]
            for pat in patterns:
                query = re.sub(pat, '', query)
        
        # Убираем окончания
        if query.endswith('е'): query = query[:-1]
        if query.endswith('у'): query = query[:-1]
        if query.endswith('ом'): query = query[:-2]
        return query.strip()

    def resolve_alias_key(self, aliases: dict, alias_name: str):
        """Возвращает ключ алиаса или None.

        Точное совпадение приоритетно. Мягкий фолбэк: нормализуем (lower, ё→е,
        пробелы) и ищем совпадения по startswith/contains и по токенам.
        При неоднозначности (несколько кандидатов) не угадываем.
        """
        if not alias_name:
            return None
        if alias_name in aliases:
            return alias_name

        def _n(s: str) -> str:
            s = (s or "").lower().replace("ё", "е")
            s = re.sub(r"\s+", " ", s).strip()
            # Лёгкое обрезание типичных окончаний, чтобы лучше матчились формы
            s = re.sub(r"(ие|ия|ье|ье|ий|ый|ой|ая|ое|ые|у|е|ом|ах|ах|ов|ев)$", "", s)
            s = re.sub(r"\s+", " ", s).strip()
            return s

        nq = _n(alias_name)
        candidates = set()

        for key in aliases.keys():
            if not key:
                continue
            nk = _n(key)
            if not nk or not nq:
                continue
            # Точные/начинающиеся
            if nk == nq or nk.startswith(nq) or nq.startswith(nk):
                candidates.add(key)
                continue
            # Вхождение
            if nq in nk or nk in nq:
                candidates.add(key)
                continue
            # По токенам: все слова запроса есть в ключе
            qtokens = {t for t in nq.split() if len(t) > 1}
            ktokens = {t for t in nk.split() if len(t) > 1}
            if qtokens and qtokens.issubset(ktokens):
                candidates.add(key)
                continue
            if ktokens and ktokens.issubset(qtokens):
                candidates.add(key)
                continue

        if len(candidates) == 1:
            return next(iter(candidates))
        if len(candidates) > 1:
            # Если неоднозначно — не угадываем, вернём None (поиск пойдёт дальше по типам)
            return None
        return None

    @staticmethod
    def spec_has_sensor_kind(spec, kind) -> bool:
        """Есть ли у устройства связанный трейт указанной величины."""
        if not spec or not kind:
            return False
        wanted = normalize_device_type(kind)
        for trait in iter_traits(spec.get("traits")):
            if normalize_device_type(trait.get("type")) != wanted:
                continue
            if (trait.get("linked_object") or "").strip() and (trait.get("linked_property") or "").strip():
                return True
        return False

    def detect_sensor_kind(self, *texts):
        """Вид величины по нескольким источникам (тип, запрос, единица) — первый найденный."""
        for text in texts:
            kind = extract_sensor_kind(text)
            if kind:
                return kind
        return None

    def find_sensor_by_kind(self, kind, rooms: list = None, text: str = ""):
        """Датчик нужной величины; комнаты перебираются в порядке приоритета.

        Если ни одна из комнат не подошла, но в тексте запроса названа комната
        одного из датчиков-кандидатов («какая температура в спальне») — берём её:
        справочник locations может быть неполным. Если кандидат один во всём доме,
        возвращается он, иначе None — не угадываем.
        """
        if not kind:
            return None
        candidates = []
        for device in self.load_devices_from_db():
            spec = self.device_to_spec(device)
            if self.spec_has_sensor_kind(spec, kind):
                candidates.append(spec)
        if not candidates:
            return None
        for room in (rooms or []):
            if not room:
                continue
            for spec in candidates:
                if room_matches(spec.get("room"), room):
                    return spec
        if text:
            # Комната, названная прямо в вопросе: сверяем со комнатами кандидатов
            # (на случай, если её нет в справочнике locations или она названа иначе).
            tokens = [stem(t) for t in normalize_text(text).split() if t]
            for spec in candidates:
                parts = [stem(p) for p in normalize_text(spec.get("room") or "").split() if p]
                if parts and all(any(tokens_match(t, p) for t in tokens) for p in parts):
                    return spec
        return candidates[0] if len(candidates) == 1 else None

    def sensor_candidates_report(self, kind, limit: int = 20) -> list:
        """Строки для лога: у кого какие метрики и есть ли привязка объекта/свойства.

        Нужно, чтобы по логу понять, почему датчик величины не находится: метрики
        нужного вида нет вовсе, либо метрика есть, но без привязанного объекта
        или свойства (тогда устройство в списке есть, а датчиком не считается).
        """
        wanted = normalize_device_type(kind)
        lines = []
        for device in self.load_devices_from_db():
            spec = self.device_to_spec(device)
            type_norm = normalize_device_type(spec.get("type"))
            has_wanted = False
            traits_info = []
            for trait in iter_traits(spec.get("traits")):
                trait_type = normalize_device_type(trait.get("type"))
                if not trait_type:
                    continue
                linked = bool((trait.get("linked_object") or "").strip()
                              and (trait.get("linked_property") or "").strip())
                mark = "привязано" if linked else "БЕЗ ПРИВЯЗКИ"
                if trait_type == wanted:
                    has_wanted = True
                    mark = "ИСКОМОЕ, " + ("привязано" if linked else "БЕЗ ПРИВЯЗКИ")
                traits_info.append(f"{trait_type}: {mark}")
            if not has_wanted and normalize_device_type(spec.get("type")) != "sensor":
                continue
            title = spec.get("title") or "?"
            room = spec.get("room") or "без комнаты"
            traits_text = "; ".join(traits_info) if traits_info else "метрик нет"
            lines.append(
                f"{title} [{room}] type={normalize_device_type(spec.get('type')) or '?'} -> {traits_text}"
            )
            if limit and len(lines) >= limit:
                break
        return lines

    def find_device_by_category_and_type(self, alias_name: str, preferred_categories: list = None, required_type: list = None, name: str = None, room: str = None):
        """Поиск устройства с учётом типа и категории.

        Порядок: требуемый тип -> название (config.name) ->
        предпочтительные категории -> первое устройство по алиасу.

        room — если задана, устройства из этой комнаты рассматриваются первыми
        (комната колонки, от которой пришла команда).
        """
        aliases = self.load_aliases_from_db()
        alias_key = self.resolve_alias_key(aliases, alias_name)
        if alias_key is None:
            return None

        specs = aliases[alias_key]
        if room:
            # Устройства из нужной комнаты — вперёд, порядок остальных сохраняем.
            specs = sorted(specs, key=lambda s: 0 if room_matches(s.get("room"), room) else 1)

        # Ищем по требуемому типу (с учётом префикса devices.types. и семейств типов)
        if required_type:
            for spec in specs:
                if device_type_matches(spec.get("type"), required_type):
                    return spec

        # Ищем по названию
        if name:
            name_lower = str(name).lower()
            for alias, alias_specs in aliases.items():
                for spec in alias_specs:
                    spec_config = spec.get("config")
                    if not isinstance(spec_config, dict):
                        continue
                    if str(spec_config.get("name", "")).lower() == name_lower:
                        return spec

        # Ищем по предпочтительным категориям/типам
        # (раньше сравнивался словарь config со строками — условие не срабатывало никогда)
        if preferred_categories:
            preferred = {str(p).strip().lower() for p in preferred_categories if str(p).strip()}
            for spec in specs:
                category = str(spec.get("category") or "").lower()
                if category and (category in preferred or (DEVICES_TYPES_PREFIX + category) in preferred):
                    return spec
                if device_type_matches(spec.get("type"), preferred):
                    return spec

        # Если не нашли ни по категории, ни по типу, возвращаем первую
        return specs[0] if specs else None

    def find_speaker_in_room(self, room):
        """Ищет устройство-колонку в указанной комнате.

        Возвращает спецификацию устройства или None. Колонкой считается
        устройство из SPEAKER_TYPES (в т.ч. «Колонка (XiaoZhi)»).
        """
        if not room:
            return None
        for device in self.load_devices_from_db():
            if not device_type_matches(device.get("type"), SPEAKER_TYPES):
                continue
            if room_matches(device.get("room"), room):
                return self.device_to_spec(device)
        return None

    def resolve_speaker(self, room="", device=""):
        """Находит колонку: по псевдониму/синониму (device) или по комнате (room).

        Порядок: сначала конкретное устройство (если задано), затем колонка в
        комнате. Возвращает спецификацию устройства или None.
        """
        if device:
            spec = self.find_device_by_category_and_type(
                self.normalize_query(device), preferred_categories=SPEAKER_TYPES
            )
            if spec and device_type_matches(spec.get("type"), SPEAKER_TYPES):
                return spec
        if room:
            return self.find_speaker_in_room(room)
        return None

    def say_on_spec(self, device_spec, text: str) -> bool:
        """Озвучивает текст на конкретном устройстве-колонке."""
        if not device_spec:
            return False
        target_object = device_spec.get("object")
        if not target_object:
            return False
        resp = self.call_majordomo("GET", f"method/{target_object}.say", params={"text": text})
        return resp is not None and resp.status_code == 200

    def say_via_tts(self, text: str, room: str = None) -> bool:
        """Озвучивает текст через колонку в комнате.

        Если комната не задана явно, используется комната колонки-источника
        (определяется по MAC в set_request_context), а при её отсутствии —
        комната по умолчанию («комната отдыха»), как раньше.
        """
        target_room = room or self._current_room or "комната отдыха"
        device_spec = self.find_speaker_in_room(target_room)
        if not device_spec:
            # Обратная совместимость: раньше колонка искалась по алиасу,
            # совпадающему с названием комнаты.
            device_spec = self.find_device_by_category_and_type(
                self.normalize_query(target_room),
                preferred_categories=SPEAKER_TYPES,
            )
        if not device_spec:
            logger.warning(f"Колонка не найдена для комнаты: {target_room}")
            return False
        return self.say_on_spec(device_spec, text)

    def match_speaker_mac(self, device_id):
        """Сопоставляет device_id из XiaoZhi с известным MAC/ID колонки.

        Логика та же, что и при определении комнаты: точное совпадение, затем
        поиск известного MAC по вхождению (кроме чисто цифровых ID, чтобы
        короткий ID XiaoZhi не «нашёлся» внутри чужого MAC). Возвращает
        известный ключ или "".
        """
        mac = normalize_mac(device_id)
        if not mac:
            return ""
        self.load_devices_from_db()
        if mac in self._rooms_by_mac:
            return mac
        if mac.isdigit():
            return ""
        for known_mac in self._rooms_by_mac:
            if known_mac and (known_mac in mac or mac in known_mac):
                return known_mac
        return ""

    def resolve_room_by_device_id(self, device_id):
        """Определяет комнату колонки по device_id из XiaoZhi.

        В поле MAC устройства хранится MAC колонки либо её ID в xiaozhi.me —
        возвращается комната устройства, чей идентификатор совпал, либо ""
        (не задан/не найден/у устройства нет комнаты). Пустая строка
        означает прежнее поведение — команда выполняется по общим правилам.

        Устойчиво к прошивкам, которые присылают не «чистый» идентификатор, а
        длинную строку с добавками: сначала точное совпадение, затем поиск
        известного MAC по вхождению в присланную строку (и наоборот).
        """
        mac = normalize_mac(device_id)
        if not mac:
            return ""
        # load_devices_from_db() наполняет индекс идентификатор -> комната (и кэширует его).
        self.load_devices_from_db()
        if mac in self._rooms_by_mac:
            return self._rooms_by_mac[mac]
        # Чисто цифровой device_id — это ID устройства XiaoZhi, а не MAC:
        # точное совпадение уже проверено, по вхождению не ищем, чтобы
        # короткий ID не «нашёлся» внутри чужого MAC.
        if mac.isdigit():
            return ""
        # После обновления прошивки колонка может присылать device_id вида
        # "AA:BB:...:CC-1234" или с префиксом — пробуем найти MAC по вхождению.
        for known_mac, known_room in self._rooms_by_mac.items():
            if known_mac and (known_mac in mac or mac in known_mac):
                logger.info(f"Комната по MAC '{known_mac}' (неточное совпадение device_id='{device_id}')")
                return known_room
        return ""

    # === Команды колонкам: озвучка и громкость =============================

    def ensure_speaker_queue_table(self, conn):
        """Создаёт таблицу очереди команд колонкам при необходимости."""
        try:
            cursor = conn.cursor()
            cursor.execute(SPEAKER_QUEUE_TABLE_DDL + table_engine_suffix(cursor))
            conn.commit()
        except Exception as e:
            logger.error(f"Ошибка создания таблицы mcp_speaker_queue: {e}")

    @staticmethod
    def speaker_target(spec):
        """Идентификатор колонки для очереди: ID (поле MAC), комната, название."""
        spec = spec or {}
        return {
            "id": normalize_mac(spec.get("mac")),
            "room": spec.get("room") or "",
            "title": spec.get("title") or "",
        }

    def speaker_http_config(self):
        """Адрес и токен внешнего канала управления колонками (если настроен)."""
        url = str(self.config.get("SPEAKER_API_URL") or "").strip()
        token = str(self.config.get("SPEAKER_API_TOKEN") or "").strip()
        return url, token

    def send_speaker_http(self, target, command, value="", message=""):
        """Отправляет команду колонке во внешний HTTP-мост.

        Настраивается полями модуля SPEAKER_API_URL / SPEAKER_API_TOKEN.
        Контракт: POST <SPEAKER_API_URL> с JSON
        {device_id, room, title, command, value, message}; при заданном токене —
        заголовок Authorization: Bearer <token>. Ответ 2xx = успех.
        Возвращает True/False, либо None — если канал не настроен.
        """
        url, token = self.speaker_http_config()
        if not url:
            return None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = {
            "device_id": target.get("id") or "",
            "room": target.get("room") or "",
            "title": target.get("title") or "",
            "command": command,
            "value": "" if value is None else str(value),
            "message": message or "",
        }
        try:
            import requests
            resp = requests.post(url, json=payload, headers=headers, timeout=5)
            return resp is not None and 200 <= resp.status_code < 300
        except Exception as e:
            logger.warning(f"HTTP-канал колонки недоступен: {e}")
            return False

    def apply_speaker_via_mdm(self, spec, command, value="", message=""):
        """Пробует выполнить команду через связанный объект MajorDoMo.

        * say    -> method/<object>.say (объект-озвучка);
        * volume -> data/<object>.<property> связанного trait'а 'volume'.
        Возвращает True при успехе; False — если подходящей привязки нет.
        """
        if not spec:
            return False
        if command == "say":
            return self.say_on_spec(spec, message)
        if command == "volume":
            for trait in iter_traits(spec.get("traits")):
                if normalize_device_type(trait.get("type")) != "volume":
                    continue
                obj = (trait.get("linked_object") or "").strip()
                prop = (trait.get("linked_property") or "").strip()
                if not (obj and prop):
                    continue
                resp = self.call_majordomo(
                    "POST", f"data/{obj}.{prop}", data={"data": str(value)}
                )
                return resp is not None and resp.status_code == 200
        return False

    def queue_speaker_command(self, target, command, value="", message=""):
        """Ставит команду колонке в очередь (до следующего обращения к ней)."""
        conn = self.get_db_connection()
        if not conn:
            return None
        try:
            self.ensure_speaker_queue_table(conn)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO mcp_speaker_queue "
                "(TARGET_ID, ROOM, TITLE, COMMAND, MESSAGE, VALUE, CHANNEL, STATUS) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'queue', 'pending')",
                (
                    target.get("id") or "",
                    target.get("room") or "",
                    target.get("title") or "",
                    command,
                    message or "",
                    "" if value is None else str(value),
                ),
            )
            new_id = cursor.lastrowid
            conn.commit()
            return new_id
        except Exception as e:
            logger.error(f"Ошибка постановки команды колонке в очередь: {e}")
            return None
        finally:
            conn.close()

    def dispatch_speaker_command(self, room="", device="", command="say", value="", message=""):
        """Выполняет команду колонке: прямые каналы, при их отсутствии — очередь.

        Прямые каналы: HTTP-мост (SPEAKER_API_URL) и связанный объект MajorDoMo.
        Если ни один не сработал — команда ставится в очередь и прозвучит/применится
        при следующем обращении к этой колонке.
        Возвращает dict: {status: sent|queued|error, channel, room, title, ...}.
        """
        speaker = self.resolve_speaker(room=room, device=device)
        if not speaker:
            available = sorted({
                d.get("room") for d in self.load_devices_from_db()
                if device_type_matches(d.get("type"), SPEAKER_TYPES) and d.get("room")
            })
            return {"status": "error", "error": "Колонка не найдена",
                    "available_rooms": available}
        target = self.speaker_target(speaker)

        if self.send_speaker_http(target, command, value=value, message=message):
            return {"status": "sent", "channel": "http", **target}
        if self.apply_speaker_via_mdm(speaker, command, value=value, message=message):
            return {"status": "sent", "channel": "mdm", **target}

        new_id = self.queue_speaker_command(target, command, value=value, message=message)
        if not new_id:
            return {"status": "error",
                    "error": "Не удалось поставить команду в очередь", **target}
        return {"status": "queued", "channel": "queue", "queue_id": new_id, **target}

    def pop_speaker_actions(self, device_id):
        """Забирает из очереди команды для колонки, от которой пришёл запрос.

        Совпадение по ID устройства (поле MAC), а если он неизвестен — по
        комнате колонки. Найденные команды помечаются доставленными.
        """
        if not device_id:
            return []
        key = self.match_speaker_mac(device_id)
        room = "" if key else self.resolve_room_by_device_id(device_id)
        if not key and not room:
            return []
        conn = self.get_db_connection()
        if not conn:
            return []
        try:
            self.ensure_speaker_queue_table(conn)
            cursor = conn.cursor()
            if key:
                cursor.execute(
                    "SELECT ID, COMMAND, MESSAGE, VALUE FROM mcp_speaker_queue "
                    "WHERE STATUS='pending' AND TARGET_ID=%s ORDER BY ID",
                    (key,),
                )
            else:
                cursor.execute(
                    "SELECT ID, COMMAND, MESSAGE, VALUE FROM mcp_speaker_queue "
                    "WHERE STATUS='pending' AND ROOM=%s ORDER BY ID",
                    (room,),
                )
            rows = cursor.fetchall()
            actions = []
            ids = []
            for row in rows:
                action = self.speaker_row_to_action(row)
                if action:
                    actions.append(action)
                ids.append(row[0])
            if ids:
                placeholders = ",".join(["%s"] * len(ids))
                cursor.execute(
                    "UPDATE mcp_speaker_queue SET STATUS='delivered', DELIVERED=NOW() "
                    f"WHERE ID IN ({placeholders})",
                    ids,
                )
                conn.commit()
            if actions:
                logger.info(f"Колонке {key or room} отдано команд из очереди: {len(actions)}")
            return actions
        except Exception as e:
            logger.error(f"Ошибка выборки очереди команд колонке: {e}")
            return []
        finally:
            conn.close()

    @staticmethod
    def speaker_row_to_action(row):
        """Строка очереди -> действие для колонки (озвучка/громкость)."""
        if not row:
            return None
        command, message, value = row[1], row[2], row[3]
        if command == "say":
            return {"action": "say", "text": message or ""}
        if command == "volume":
            try:
                return {"action": "set_volume", "value": int(value)}
            except (TypeError, ValueError):
                return {"action": "set_volume", "value": SPEAKER_DEFAULT_VOLUME}
        return None

    def register_speaker_id(self, device_id):
        """Записывает ID колонки прямо в поле ID устройства (mcp_devices.MAC).

        Вызывается голосовым инструментом identify_speaker, пока админка
        держит режим ожидания (кнопка «Определить колонку» → таблица
        mcp_detect, строка ID=1).

        Сценарии:
          * TARGET_ID > 0 (форма редактирования) — ID пишется в поле этого
            устройства;
          * TARGET_ID = 0 (форма добавления) — создаётся новое устройство
            типа «Колонка (XiaoZhi)» с заполненным полем ID;
          * ID уже занят другой колонкой — ничего не пишем, возвращаем её
            комнату: «колонка уже определена в комнате».

        Возвращает dict со статусом: 'ok' | 'already' | 'expired' |
        'idle' (кнопка не нажималась) | 'error'.
        """
        norm = normalize_mac(device_id)
        if not norm:
            return {"status": "error", "message": "Пустой device_id"}
        conn = self.get_db_connection()
        if not conn:
            return {"status": "error", "message": "Нет соединения с БД"}
        try:
            with conn.cursor() as cursor:
                cursor.execute(DETECT_TABLE_DDL + table_engine_suffix(cursor))
                cursor.execute(
                    "SELECT TARGET_ID, TS, TITLE, ALIACE, LOCATION FROM mcp_detect WHERE ID=1"
                )
                row = cursor.fetchone()
                if not row:
                    return {"status": "idle"}
                target_id = int(row[0] or 0)
                armed_ts = int(row[1] or 0)
                title = row[2] or ""
                aliace = row[3] or ""
                location = row[4] or ""
                if not armed_ts or (int(time.time()) - armed_ts) > DETECT_WINDOW_SECONDS:
                    return {"status": "expired"}

                # ID уже занят другой колонкой? (нормализация как в резолвере)
                cursor.execute(
                    "SELECT ID, TITLE, ROOM, MAC FROM mcp_devices WHERE MAC <> ''"
                )
                for dev_id, dev_title, dev_room, dev_mac in cursor.fetchall():
                    if int(dev_id) == target_id:
                        continue
                    if normalize_mac(dev_mac) == norm:
                        cursor.execute(
                            "UPDATE mcp_detect SET RESULT=%s, RESULT_DEVICE_ID=%s, "
                            "RESULT_ROOM=%s, RESULT_TITLE=%s, NEW_ID=0 WHERE ID=1",
                            ("already", device_id, dev_room or "", dev_title or ""),
                        )
                        conn.commit()
                        logger.info(
                            f"ID '{device_id}' уже висит на устройстве "
                            f"#{dev_id} '{dev_title}' (комната '{dev_room}') — не записываем"
                        )
                        return {
                            "status": "already",
                            "device_id": device_id,
                            "room": dev_room or "",
                            "title": dev_title or "",
                        }

                if target_id > 0:
                    cursor.execute(
                        "UPDATE mcp_devices SET MAC=%s WHERE ID=%s",
                        (device_id, target_id),
                    )
                    cursor.execute(
                        "UPDATE mcp_detect SET RESULT='ok', RESULT_DEVICE_ID=%s, "
                        "RESULT_ROOM='', RESULT_TITLE='', NEW_ID=0 WHERE ID=1",
                        (device_id,),
                    )
                    conn.commit()
                    # Поле изменилось — сбрасываем кэши устройств/индекса комнат.
                    self._devices_cache = None
                    self._rooms_by_mac = {}
                    logger.info(f"ID колонки '{device_id}' записан в устройство #{target_id}")
                    return {"status": "ok", "device_id": device_id, "new_id": 0}

                # Форма добавления: устройство ещё не сохранено — создаём его.
                if not title:
                    title = "Колонка (новая)"
                if not aliace:
                    aliace = title
                cursor.execute(
                    "INSERT INTO mcp_devices (TITLE, ALIACE, TYPE, ROOM, MAC) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (title, aliace, "audio.speaker", location, device_id),
                )
                new_id = int(cursor.lastrowid or 0)
                cursor.execute(
                    "UPDATE mcp_detect SET RESULT='ok', RESULT_DEVICE_ID=%s, "
                    "RESULT_ROOM='', RESULT_TITLE='', NEW_ID=%s WHERE ID=1",
                    (device_id, new_id),
                )
                conn.commit()
                self._devices_cache = None
                self._rooms_by_mac = {}
                logger.info(f"Создано устройство-колонка #{new_id} с ID '{device_id}'")
                return {"status": "ok", "device_id": device_id, "new_id": new_id}
        except Exception as e:
            logger.error(f"Ошибка записи ID колонки '{device_id}': {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return {"status": "error", "message": str(e)}
        finally:
            conn.close()

    def set_request_context(self, device_id=""):
        """Запоминает комнату колонки-источника (по MAC) для TTS-ответов.

        Вызывается в начале обработки голосовой команды. Возвращает комнату
        (пустая строка — колонка не привязана/не найдена, работаем как раньше).
        """
        self._current_room = self.resolve_room_by_device_id(device_id)
        if device_id or self._current_room:
            logger.info(f"Колонка device_id='{device_id}' → комната: '{self._current_room}'")
        return self._current_room

    # === ГОЛОСОВОЕ УПРАВЛЕНИЕ ПЛАНИРОВЩИКОМ ===

    def ensure_schedule_table(self, conn):
        """Создаёт таблицу планировщика и добавляет недостающие колонки (лениво)."""
        try:
            with conn.cursor() as cursor:
                cursor.execute(SCHEDULE_TABLE_DDL + table_engine_suffix(cursor))
                cursor.execute("SHOW COLUMNS FROM mcp_schedule")
                columns = {str(row[0]).upper() for row in cursor.fetchall()}
                if "LAST_RUN" not in columns:
                    cursor.execute("ALTER TABLE mcp_schedule ADD COLUMN LAST_RUN datetime DEFAULT NULL")
                if "SCHEDULE_DATE" not in columns:
                    cursor.execute("ALTER TABLE mcp_schedule ADD COLUMN SCHEDULE_DATE date DEFAULT NULL")
            conn.commit()
        except Exception as e:
            logger.error(f"Не удалось подготовить таблицу mcp_schedule: {e}")

    def load_schedule_from_db(self):
        """Загружает расписание из таблицы mcp_schedule."""
        conn = self.get_db_connection()
        if not conn:
            logger.error("Не удалось подключиться к БД для загрузки расписания")
            return []

        try:
            self.ensure_schedule_table(conn)
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM mcp_schedule ORDER BY CREATED DESC, ID DESC")
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]

            schedule = []
            for row in rows:
                record = {columns[i]: row[i] for i in range(len(columns))}

                raw_days = record.get("SCHEDULE_DAYS")
                parsed_days = safe_json_loads(raw_days, None)
                days = normalize_days(parsed_days if parsed_days is not None else raw_days)

                action_data = safe_json_loads(record.get("ACTION_DATA"), {})
                if not isinstance(action_data, dict):
                    action_data = {}

                task = {
                    "id": record.get("TASK_ID") or str(record.get("ID") or ""),
                    "row_id": record.get("ID"),
                    "enabled": bool(record.get("ENABLED")),
                    "description": record.get("TITLE") or "",
                    "time": record.get("SCHEDULE_TIME") or "",
                    "days": days,
                    "schedule_date": str(record.get("SCHEDULE_DATE") or "") or None,
                    "last_run": str(record.get("LAST_RUN") or "") or None,
                    "action": {
                        "type": record.get("ACTION_TYPE") or "",
                        "device": record.get("ACTION_TARGET") or "",
                        "state": record.get("ACTION_STATE") or "",
                        "data": action_data,
                    },
                }
                schedule.append(task)

            return schedule
        except Exception as e:
            logger.error(f"Ошибка загрузки расписания из БД: {e}")
            return []
        finally:
            conn.close()

    def _build_schedule_fields(self, task):
        """Готовит набор колонок mcp_schedule из словаря задания."""
        action = task.get("action") or {}
        action_type = str(action.get("type") or "device")
        action_target = str(action.get("device") or "")
        state = str(action.get("state") or "")
        is_on = state.strip().lower() in ("включи", "включить", "вкл", "on", "1", "да", "true")

        data = action.get("data")
        if not isinstance(data, dict) or not data.get("linked_object"):
            device_spec = self.find_device_by_category_and_type(action_target)
            if device_spec:
                data = {
                    "type": "on" if is_on else "off",
                    "linked_object": device_spec.get("object"),
                    "linked_property": device_spec.get("property"),
                }
            else:
                data = {}
        else:
            data = dict(data)
            data.setdefault("type", "on" if is_on else "off")

        brightness = action.get("brightness")
        if brightness is not None and brightness != "":
            data["brightness"] = max(0, min(100, int(brightness)))

        days = normalize_days(task.get("days"))
        schedule_date = task.get("schedule_date")
        if days == ["once"]:
            if schedule_date:
                schedule_date = str(schedule_date)[:10]
            else:
                # Ближайшее наступление указанного времени (сегодня или завтра).
                normalized = normalize_time(task.get("time"))
                if normalized:
                    hours, minutes = (int(part) for part in normalized.split(":"))
                    now = datetime.now()
                    target = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
                    if target <= now:
                        target += timedelta(days=1)
                    schedule_date = target.strftime("%Y-%m-%d")
        else:
            schedule_date = None

        return {
            "title": str(task.get("description") or ""),
            "enabled": 1 if task.get("enabled", True) else 0,
            "time": normalize_time(task.get("time")) or str(task.get("time") or ""),
            "days": json.dumps(days, ensure_ascii=False),
            "schedule_date": schedule_date,
            "action_type": action_type,
            "action_target": action_target,
            "action_state": state,
            "action_data": json.dumps(data, ensure_ascii=False),
        }

    def save_schedule_to_db(self, schedule):
        """Сохраняет расписание в mcp_schedule.

        Изменения применяются точечно (INSERT/UPDATE/DELETE по TASK_ID), а не
        перезаписью всей таблицы: это сохраняет CREATED/LAST_RUN и исключает
        гонки при параллельных вызовах.
        """
        conn = self.get_db_connection()
        if not conn:
            logger.error("Не удалось подключиться к БД для сохранения расписания")
            return False

        try:
            self.ensure_schedule_table(conn)
            with conn.cursor() as cursor:
                cursor.execute("SELECT TASK_ID FROM mcp_schedule")
                existing_ids = {row[0] for row in cursor.fetchall() if row[0]}

                kept_ids = set()
                for task in schedule:
                    task_id = str(task.get("id") or "").strip()
                    if not task_id:
                        continue
                    kept_ids.add(task_id)
                    fields = self._build_schedule_fields(task)

                    if task_id in existing_ids:
                        cursor.execute(
                            "UPDATE mcp_schedule SET TITLE=%s, ENABLED=%s, SCHEDULE_TIME=%s,"
                            " SCHEDULE_DAYS=%s, SCHEDULE_DATE=%s, ACTION_TYPE=%s, ACTION_TARGET=%s,"
                            " ACTION_STATE=%s, ACTION_DATA=%s WHERE TASK_ID=%s",
                            (
                                fields["title"], fields["enabled"], fields["time"],
                                fields["days"], fields["schedule_date"], fields["action_type"],
                                fields["action_target"], fields["action_state"],
                                fields["action_data"], task_id,
                            ),
                        )
                    else:
                        cursor.execute(
                            "INSERT INTO mcp_schedule"
                            " (TASK_ID, TITLE, ENABLED, SCHEDULE_TIME, SCHEDULE_DAYS, SCHEDULE_DATE,"
                            " ACTION_TYPE, ACTION_TARGET, ACTION_STATE, ACTION_DATA)"
                            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (
                                task_id, fields["title"], fields["enabled"], fields["time"],
                                fields["days"], fields["schedule_date"], fields["action_type"],
                                fields["action_target"], fields["action_state"],
                                fields["action_data"],
                            ),
                        )

                for task_id in (existing_ids - kept_ids):
                    cursor.execute("DELETE FROM mcp_schedule WHERE TASK_ID=%s", (task_id,))

            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения расписания в БД: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            conn.close()

#    def reload_scheduler(self):
#        """Перезапускает сервис планировщика."""
#        try:
#            import subprocess
#            subprocess.run(["sudo", "systemctl", "restart", "mcp-scheduler"], check=True)
#        except subprocess.CalledProcessError:
#            pass  # Игнорируем ошибки, если сервис не нуждается в перезапуске

