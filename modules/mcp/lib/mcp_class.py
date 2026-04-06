#!/usr/bin/env python3
"""
Класс для работы с MajorDoMo MCP
"""
import sys
import os
import json
import logging
import re
import requests
from datetime import datetime

ALIASES_FILE = "/opt/mcp-bridge/device_aliases.json"
SCHEDULE_FILE = "/opt/mcp-bridge/schedule.json"

log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper())
logging.basicConfig(stream=sys.stderr, level=log_level, format="%(levelname)s: %(message)s")
logger = logging.getLogger("mcp_majordomo")

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
            
            # Определяем путь к config.php относительно текущего местоположения скрипта
            # Если скрипт находится в /var/www/html/modules/mcp/lib/, то нужно подняться на 2 уровня вверх
            two_up = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            config_path = os.path.join(two_up, "config.php")
            
            if not os.path.exists(config_path):
                # Альтернативный путь - от корня системы
                config_path = "/var/www/html/config.php"
                if not os.path.exists(config_path):
                    raise Exception(f"Файл конфигурации не найден: {config_path}")
            
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

    def load_aliases_from_db(self, type: str = None):
        """Загружает данные из таблицы mcp_devices"""
        conn = self.get_db_connection()
        if not conn:
            logger.warning("Не удалось подключиться к БД, используем резервный JSON")
            return self.load_aliases_from_json()
    
        try:
            with conn.cursor() as cursor:
                if type:
                    cursor.execute("SELECT ID, TITLE, ALIACE, TYPE, TRAITS, CONFIG FROM mcp_devices WHERE ALIACE != '{type}'")
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
            
                # Ищем связанные объект и свойство
                linked_object = None
                linked_property = None
                for trait_name, trait_data in traits.items():
                    if 'linked_object' in trait_data and 'linked_property' in trait_data:
                        linked_object = trait_data['linked_object']
                        linked_property = trait_data['linked_property']
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
                    
            return aliases
        except Exception as e:
            logger.error(f"Ошибка загрузки алиасов из БД: {e}, используем резервный JSON")
            return self.load_aliases_from_json()
        finally:
            conn.close()

    def load_aliases_from_json(self):
        """Загружает алиасы из старого формата JSON файла"""
        if not os.path.exists(ALIASES_FILE):
            return {}
        try:
            with open(ALIASES_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)

            aliases = {}
            for category, details in raw.items():
                if "devices" not in details:
                    continue
                for key, spec in details["devices"].items():
                    names = [name.strip().lower() for name in key.split(",")]
                    for name in names:
                        if name:
                            if name not in aliases:
                                aliases[name] = []
                            aliases[name].append({
                                "object": spec["object"],
                                "property": spec["property"],
                                "category": category,
                                "type": details.get("type", "unknown")
                            })
            return aliases
        except Exception as e:
            logger.error(f"Ошибка загрузки алиасов из JSON: {e}")
            return {}

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
        # Проверяем, содержит ли запрос информацию о температуре
        if 'температура' in query:
            # Убираем только 'температура' и частицы 'в' или 'на'
            patterns = [
                r'^температура\s+(в|на)\s+',
                r'^температура\s*'
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

    def find_device_by_category_and_type(self, alias_name: str, preferred_categories: list = None, required_type: list = None, name: str = None):
        """Поиск устройства с учётом категории и типа"""
        aliases = self.load_aliases_from_db()
        if alias_name not in aliases:
            return None

        specs = aliases[alias_name]

        # Ищем по требуемому типу
        if required_type:
            for spec in specs:
                logger.info(f"spec['config3']: {spec['type']} == {required_type}")
                if spec["type"] in required_type:
                    return spec

        # Ищем по названию
        if name:
            for alias, specs in aliases.items():
                for spec in specs:
                    if spec.get("config", {}).get("name", "").lower() == name.lower():
                        return spec

        # Ищем по предпочтительным категориям
        if preferred_categories:
            for spec in specs:
                logger.info(f"spec['config1']: {spec['config']}")
                if spec["config"] in preferred_categories:
                    logger.info(f"spec['config2']: {spec['config']}")
                    # Если требуется определённый тип, проверяем его
                    if required_type and spec["type"] not in required_type:
                        continue
                    return spec

        # Если не нашли ни по категории, ни по типу, возвращаем первую
        return specs[0] if specs else None

    def say_via_tts(self, text: str, room: str = "комната отдыха") -> bool:
        """TTS (ищет только в категории "audio.speaker")"""
        alias_name = self.normalize_query(room)
        device_spec = self.find_device_by_category_and_type(alias_name, preferred_categories=["audio.speaker"])
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
            logger.warning("Не удалось подключиться к БД для загрузки расписания, используем резервный JSON")
            return self.load_schedule()  # Используем резервный JSON
    
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
            logger.error(f"Ошибка загрузки расписания из БД: {e}, используем резервный JSON")
            return self.load_schedule()  # Используем резервный JSON
        finally:
            conn.close()


    def save_schedule_to_db(self, schedule):
        """Сохраняет расписание в таблицу mcp_schedule"""
        conn = self.get_db_connection()
        if not conn:
            logger.error("Не удалось подключиться к БД для сохранения расписания, используем резервный JSON")
            self.save_schedule(schedule)  # Сохраняем в резервный JSON
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
            logger.error(f"Ошибка сохранения расписания в БД: {e}, используем резервный JSON")
            self.save_schedule(schedule)  # Сохраняем в резервный JSON
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

    def load_schedule(self):
        """Резервная функция - загрузка из JSON файла"""
        if not os.path.exists(SCHEDULE_FILE):
            return []
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_schedule(self, schedule):
        """Резервная функция - сохранение в JSON файл"""
        with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
            json.dump(schedule, f, ensure_ascii=False, indent=2)
