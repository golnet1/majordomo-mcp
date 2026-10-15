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

DEVICES_TYPES_PREFIX = "devices.types."


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

    def load_aliases_from_db(self, type: str = None, force: bool = False):
        """Загружает данные из таблицы mcp_devices.

        Результат кэшируется на ALIASES_CACHE_TTL секунд: за один запрос к LLM
        функцию вызывают несколько раз (поиск устройства + список доступных),
        и без кэша это давало столько же подключений к БД.
        """
        if type is None and not force and self._aliases_cache is not None:
            if (time.time() - self._aliases_cache_time) < ALIASES_CACHE_TTL:
                return self._aliases_cache

        conn = self.get_db_connection()
        if not conn:
            logger.error("Не удалось подключиться к БД для загрузки алиасов")
            return {}
    
        try:
            with conn.cursor() as cursor:
                if type:
                    cursor.execute(
                        "SELECT ID, TITLE, ALIACE, TYPE, TRAITS, CONFIG FROM mcp_devices WHERE TYPE != %s",
                        (type,),
                    )
                else:
                    cursor.execute("SELECT ID, TITLE, ALIACE, TYPE, TRAITS, CONFIG FROM mcp_devices")
                rows = cursor.fetchall()

            aliases = {}
            for row in rows:
                id, title, aliace, dev_type, traits_json, config_json = row
            
                # Парсим алиасы
                names = [name.strip().lower() for name in str(aliace).split(',')] if aliace else []
            
                # Добавляем основное название тоже
                if title:
                    title_lower = title.lower()
                    if title_lower not in names:
                        names.append(title_lower)
            
                # Парсим трейты
                traits = {}
                if traits_json:
                    try:
                        traits = json.loads(traits_json)
                    except:
                        traits = {}
            
                # Парсим конфиг
                config = {}
                if config_json:
                    try:
                        config = json.loads(config_json)
                    except:
                        config = {}
            
                # Ищем связанные объект и свойство.
                # Поддерживаем оба формата TRAITS:
                # 1) dict: {"on": {...}, "temperature": {...}}
                # 2) list: [{"type":"on", ...}, {"type":"temperature", ...}]
                # Берём первый трейт, у которого заданы ОБА непустых поля
                # (раньше пустой первый трейт перебивал корректные следующие).
                linked_object = None
                linked_property = None
                if isinstance(traits, dict):
                    trait_items = list(traits.values())
                elif isinstance(traits, list):
                    trait_items = traits
                else:
                    trait_items = []
                for trait_data in trait_items:
                    if not isinstance(trait_data, dict):
                        continue
                    trait_object = (trait_data.get('linked_object') or '').strip()
                    trait_property = (trait_data.get('linked_property') or '').strip()
                    if trait_object and trait_property:
                        linked_object = trait_object
                        linked_property = trait_property
                        break
            
                if not linked_object or not linked_property:
                    # Если не найдены из трейтов, используем TITLE как объект
                    if title:
                        linked_object = title
                        linked_property = "status"  # стандартное свойство
                    else:
                        continue
            
                # Создаем спецификацию
                spec = {
                    "object": linked_object,
                    "property": linked_property,
                    "category": dev_type.split('.')[-1] if '.' in dev_type else dev_type if dev_type else 'device',
                    "type": dev_type if dev_type else 'unknown',
                    "traits": traits,  # Добавляем трейты
                    "config": config   # Добавляем конфиг
                }
            
                # Добавляем для всех алиасов
                for name in names:
                    if name:
                        if name not in aliases:
                            aliases[name] = []
                        aliases[name].append(spec)

            if type is None:
                self._aliases_cache = aliases
                self._aliases_cache_time = time.time()
            return aliases
        except Exception as e:
            logger.error(f"Ошибка загрузки алиасов из БД: {e}")
            return {}
        finally:
            conn.close()

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

