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
from datetime import datetime

log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper())
logging.basicConfig(stream=sys.stderr, level=log_level, format="%(levelname)s: %(message)s")
logger = logging.getLogger("mcp_majordomo")

# Сколько секунд держать в памяти индекс алиасов (mcp_devices).
# Спасает от повторных запросов к БД внутри одного обращения LLM,
# при этом изменения в устройствах подхватываются через несколько секунд.
ALIASES_CACHE_TTL = 5

# Кэш списка комнат держим дольше: locations меняются редко.
ROOMS_CACHE_TTL = 60

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
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
)


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

# --- Семантика голосового управления -------------------------------------
# Словарь «слово в запросе -> тип устройства» (правило: если в фразе названо
# типовое слово («свет», «шторы»), ищем по типу устройства, а не по имени).
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
    "колонк": "media_device.receiver",
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
    return False


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
        self._aliases_cache = None
        self._aliases_cache_time = 0.0
        self._devices_cache = None
        self._devices_cache_time = 0.0
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
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT ID, TITLE, ALIACE, TYPE, ROOM, TRAITS, CONFIG FROM mcp_devices")
                rows = cursor.fetchall()

            for row in rows:
                dev_id, title, aliace, dev_type, room, traits_json, config_json = row
                traits = safe_json_loads(traits_json, {})
                config = safe_json_loads(config_json, {})
                if not room and isinstance(config, dict):
                    room = config.get('room') or ''

                linked_object, linked_property = first_linked_trait(traits)
                if not linked_object or not linked_property:
                    logger.info(f"Устройство '{title}' без связанного объекта/свойства — пропущено")
                    continue

                dev_type = dev_type or 'unknown'
                devices.append({
                    "id": dev_id,
                    "title": title or '',
                    "aliases": parse_aliases(aliace),
                    "type": dev_type,
                    "category": dev_type.split('.')[-1] if '.' in dev_type else dev_type,
                    "room": room or '',
                    "object": linked_object,
                    "property": linked_property,
                    "traits": traits,
                    "config": config if isinstance(config, dict) else {},
                })
        except Exception as e:
            logger.error(f"Ошибка загрузки устройств из БД: {e}")
            return []
        finally:
            conn.close()

        self._devices_cache = devices
        self._devices_cache_time = time.time()
        return devices

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
        }

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
        """Остаток фразы без команд, типа и комнаты (для поиска по имени устройства)."""
        drop_stems = {stem(word) for word in COMMAND_WORDS}
        if device_type:
            required = device_type if isinstance(device_type, (list, tuple)) else [device_type]
            for token in normalize_text(text).split():
                if any(device_type_matches(TYPE_WORDS.get(stem(token), ''), [req]) for req in required):
                    drop_stems.add(stem(token))
        else:
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

    def resolve_devices(self, query="", device_type=None, room=None, scope=None):
        """Единый резолвер: фраза -> устройства либо запрос на уточнение.

        Возвращает словарь со status:
          ok          — devices (список спецификаций), найден однозначный набор;
          need_room   — rooms (комнаты, где есть подходящие устройства);
          need_device — candidates (варианты внутри комнаты);
          not_found   — available (что вообще есть по фильтру).
        """
        devices = self.load_devices_from_db()
        rooms = self.load_rooms_from_db()
        text = normalize_text(query)

        if not room:
            room = self.extract_room(text, rooms)

        if not device_type:
            device_type = self.extract_device_type(text)
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
                cursor.execute(SCENES_TABLE_DDL)
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
        """Находит сцену по имени/синониму. Возвращает (scene, ambiguous)."""
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

        results = []
        resp = self.call_majordomo(
            "POST", f"data/{spec['object']}.{spec['property']}", data={"data": state[0]}
        )
        if not resp or resp.status_code != 200:
            return {"success": False, "target": spec.get("title"),
                    "error": f"MajorDoMo error: {resp.status_code if resp else 'timeout'}"}
        results.append(f"{spec['object']}.{spec['property']}={state[0]}")

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
                    "POST", f"data/{on_trait['object']}.{on_trait['property']}", data={"data": "1"}
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

        Точное совпадение приоритетно. Если его нет — мягкий фолбэк:
        нормализатор отсекает окончания ("улиц" -> "улица", "бан" -> "баня"),
        поэтому ищем единственный ключ, который начинается с запроса или
        наоборот. При неоднозначности (несколько кандидатов) не угадываем.
        """
        if alias_name in aliases:
            return alias_name
        if not alias_name:
            return None
        candidates = sorted(
            {key for key in aliases if key and (key.startswith(alias_name) or alias_name.startswith(key))}
        )
        if len(candidates) == 1:
            return candidates[0]
        return None

    def find_device_by_category_and_type(self, alias_name: str, preferred_categories: list = None, required_type: list = None, name: str = None):
        """Поиск устройства с учётом типа и категории.

        Порядок: требуемый тип -> название (config.name) ->
        предпочтительные категории -> первое устройство по алиасу.
        """
        aliases = self.load_aliases_from_db()
        alias_key = self.resolve_alias_key(aliases, alias_name)
        if alias_key is None:
            return None

        specs = aliases[alias_key]

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

    def say_via_tts(self, text: str, room: str = "комната отдыха") -> bool:
        """TTS (ищет колонку/медиаустройство в комнате)"""
        alias_name = self.normalize_query(room)
        device_spec = self.find_device_by_category_and_type(
            alias_name,
            preferred_categories=[
                "audio.speaker", "speaker",
                "media_device", "media_device.receiver",
                "media_device.tv", "media_device.tv_box",
            ],
        )
        if not device_spec:
            logger.warning(f"Колонка не найдена для комнаты: {room}")
            return False

        resp = self.call_majordomo("GET", f"method/{device_spec['object']}.say", params={"text": text})
        return resp is not None and resp.status_code == 200

    # === ГОЛОСОВОЕ УПРАВЛЕНИЕ ПЛАНИРОВЩИКОМ ===

    def load_schedule_from_db(self):
        """Загружает расписание из таблицы mcp_schedule"""
        conn = self.get_db_connection()
        if not conn:
            logger.error("Не удалось подключиться к БД для загрузки расписания")
            return []
    
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT TASK_ID, TITLE, ENABLED, SCHEDULE_TIME, SCHEDULE_DAYS, 
                           ACTION_TYPE, ACTION_TARGET, ACTION_STATE, ACTION_DATA, CREATED
                    FROM mcp_schedule 
                    ORDER BY CREATED DESC
                """)
                rows = cursor.fetchall()

            schedule = []
            for row in rows:
                task_id, title, enabled, time_str, days_str, action_type, action_target, action_state, action_data, created = row
            
                # Парсим дни
                try:
                    days = json.loads(days_str) if days_str else []
                except:
                    days = days_str.split(',') if days_str else []
                
                # Парсим данные действия
                try:
                    action_data_parsed = json.loads(action_data) if action_data else {}
                except:
                    action_data_parsed = action_data if action_data else {}
            
                task = {
                    "id": task_id,
                    "enabled": bool(enabled),
                    "description": title,
                    "time": time_str,
                    "days": days,
                    "action": {
                        "type": action_type,
                        "device": action_target,
                        "state": action_state,
                        "data": action_data_parsed
                    }
                }
                schedule.append(task)
            
            return schedule
        except Exception as e:
            logger.error(f"Ошибка загрузки расписания из БД: {e}")
            return []
        finally:
            conn.close()


    def save_schedule_to_db(self, schedule):
        """Сохраняет расписание в таблицу mcp_schedule"""
        conn = self.get_db_connection()
        if not conn:
            logger.error("Не удалось подключиться к БД для сохранения расписания")
            return False

        try:
            with conn.cursor() as cursor:
                # Очищаем таблицу
                cursor.execute("DELETE FROM mcp_schedule")
                
                # Вставляем задачи
                for task in schedule:
                    task_id = task["id"]
                    title = task["description"]
                    enabled = 1 if task["enabled"] else 0
                    time_str = task["time"]
                    days_str = json.dumps(task["days"]) if isinstance(task["days"], list) else ','.join(task["days"]) if isinstance(task["days"], list) else task["days"]
                    action = task["action"]
                    
                    # Подготовка ACTION_DATA в зависимости от типа действия
                    action_data_dict = {}
                    if action["type"] == "device":
                        # Найдем спецификацию устройства для получения связанных объекта и свойства
                        device_spec = self.find_device_by_category_and_type(action["device"])
                        if device_spec:
                            # Определяем тип действия (on/off) в зависимости от слова в команде
                            action_type = "on" if action["state"].lower() in ["включи", "включить", "on", "1", "да"] else "off"
                            action_data_dict = {
                                "type": action_type,
                                "linked_object": device_spec["object"],
                                "linked_property": device_spec["property"]
                            }
                        else:
                            # Если не найдено спецификации, используем стандартную структуру
                            action_type = "on" if action["state"].lower() in ["включи", "включить", "on", "1", "да"] else "off"
                            action_data_dict = {
                                "type": action_type,
                                "linked_object": action["device"],
                                "linked_property": "status"
                            }
                    
                    cursor.execute("""
                        INSERT INTO mcp_schedule 
                        (TASK_ID, TITLE, ENABLED, SCHEDULE_TIME, SCHEDULE_DAYS, ACTION_TYPE, ACTION_TARGET, ACTION_STATE, ACTION_DATA)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        task_id, title, enabled, time_str, days_str,
                        action["type"], action["device"], action["state"], json.dumps(action_data_dict)
                    ))
                
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Ошибка сохранения расписания в БД: {e}")
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

