<?php

$this->devices_type = [
   'media_device' => [
      'device_name' => 'media_device',
      'description' => 'Аудио и видеотехника'
   ],
   'media_device.receiver' => [
      'device_name' => 'media_device.receiver',
      'description' => 'AV-ресивер'
   ],
   'sensor' => [
      'device_name' => 'sensor',
      'description' => 'Датчик'
   ],
   'openable' => [
      'device_name' => 'openable',
      'description' => 'Дверь, ворота, окно'
   ],
   'thermostat.ac' => [
      'device_name' => 'thermostat.ac',
      'description' => 'Кондиционер'
   ],
   'other' => [
      'device_name' => 'other',
      'description' => 'Остальные устройства'
   ],
   'switch' => [
      'device_name' => 'switch',
      'description' => 'Переключатель'
   ],
   'vacuum_cleaner' => [
      'device_name' => 'vacuum_cleaner',
      'description' => 'Пылесос'
   ],
   'socket' => [
      'device_name' => 'socket',
      'description' => 'Розетка'
   ],
   'light' => [
      'device_name' => 'light',
      'description' => 'Свет'
   ],
   'media_device.tv' => [
      'device_name' => 'media_device.tv',
      'description' => 'Телевизор'
   ],
   'media_device.tv_box' => [
      'device_name' => 'media_device.tv_box',
      'description' => 'ТВ-приставка'
   ],
   'thermostat' => [
      'device_name' => 'thermostat',
      'description' => 'Термостат'
   ],
   'cooking.kettle' => [
      'device_name' => 'cooking.kettle',
      'description' => 'Чайник'
   ],
   'openable.curtain' => [
      'device_name' => 'openable.curtain',
      'description' => 'Шторы, жалюзи'
   ]
];

$this->devices_instance = [
   'on' => [
      'instance_name' => 'on',
      'description' => 'Включить/выключить',
      'capability' => 'on_off',
      'default_value' => 0,
      'parameters' => [
         'split' => false,
      ]
   ],
   'humidity' => [
      'instance_name' => 'humidity',
      'description' => 'Влажность',
      'capability' => 'range',
      'default_value' => 0,
      'parameters' => [
         'unit' => 'unit.percent',
         'range' => [
            'min' => 0,
            'max' => 100,
            'precision' => 5
         ]
      ]
   ],
   'volume' => [
      'instance_name' => 'volume',
      'description' => 'Громкость',
      'capability' => 'range',
      'default_value' => 1,
      'parameters' => [
         'range' => [
            'min' => 1,
            'max' => 100,
            'precision' => 1
         ]
      ]
   ],
   'input_source' => [
      'instance_name' => 'input_source',
      'description' => 'Источник сигнала',
      'capability' => 'mode',
      'default_value' => 'one',
      'parameters' => [
         'modes' => [
            ['value' => 'one'],
            ['value' => 'two'],
            ['value' => 'three'],
            ['value' => 'four'],
            ['value' => 'five']
         ],
         'ordered' => false
      ]
   ],
   'pause' => [
      'instance_name' => 'pause',
      'description' => 'Пауза',
      'capability' => 'toggle',
      'default_value' => false
   ],
   'backlight' => [
      'instance_name' => 'backlight',
      'description' => 'Подсветка',
      'capability' => 'toggle',
      'default_value' => false
   ],
   'mute' => [
      'instance_name' => 'mute',
      'description' => 'Режим без звука',
      'capability' => 'toggle',
      'default_value' => false
   ],
   'keep_warm' => [
      'instance_name' => 'keep_warm',
      'description' => 'Режим поддержания тепла',
      'capability' => 'toggle',
      'default_value' => false
   ],
   'fan_speed' => [
      'instance_name' => 'fan_speed',
      'description' => 'Скорость вентиляции',
      'capability' => 'mode',
      'parameters' => [
         'modes' => [
            ['value' => 'auto'],
            ['value' => 'low'],
            ['value' => 'medium'],
            ['value' => 'high']
         ],
         'ordered' => true
      ]
   ],
   'open' => [
      'instance_name' => 'open',
      'description' => 'Степень открытия',
      'capability' => 'range',
      'default_value' => 0,
      'parameters' => [
         'unit' => 'unit.percent',
         'range' => [
            'min' => 0,
            'max' => 100,
            'precision' => 10
         ]
      ]
   ],
   'channel' => [
      'instance_name' => 'channel',
      'description' => 'ТВ-канал',
      'capability' => 'range',
      'default_value' => 1,
      'parameters' => [
         'range' => [
            'min' => 0,
            'max' => 999,
            'precision' => 1
         ]
      ]
   ],
   'temperature' => [
      'instance_name' => 'temperature',
      'description' => 'Температура',
      'capability' => 'range',
      'default_value' => 20,
      'parameters' => [
         'unit' => 'unit.temperature.celsius',
         'range' => [
            'min' => 1,
            'max' => 100,
            'precision' => 1
         ]
      ]
   ],
   'thermostat' => [
      'instance_name' => 'thermostat',
      'description' => 'Температурный режим',
      'capability' => 'mode',
      'parameters' => [
         'modes' => [
            ['value' => 'auto'],
            ['value' => 'heat'],
            ['value' => 'cool'],
            ['value' => 'eco'],
            ['value' => 'dry'],
            ['value' => 'fan_only'],
            ['value' => 'turbo'],
         ],
         'ordered' => true
      ]
   ],
   'temperature_k' => [
      'instance_name' => 'temperature_k',
      'description' => 'Цветовая температура',
      'capability' => 'color_setting',
      'default_value' => 4500,
      'parameters' => [
         'temperature_k' => [
            'min' => 2700,
            'max' => 9000,
            'precision' => 1
         ]
      ]
   ],
   'rgb' => [
      'instance_name' => 'rgb',
      'description' => 'Цвет в формате RGB',
      'capability' => 'color_setting',
      'default_value' => '000000',
      'parameters' => [
         'color_model' => 'rgb'
      ]
   ],
   'brightness' => [
      'instance_name' => 'brightness',
      'description' => 'Яркость',
      'capability' => 'range',
      'default_value' => 50,
      'parameters' => [
         'unit' => 'unit.percent',
         'range' => [
            'min' => 1,
            'max' => 100,
            'precision' => 1
         ]
      ]
   ],
   'amperage_sensor' => [
      'instance_name' => 'amperage_sensor',
      'description' => 'Сила тока',
      'capability' => 'float',
      'default_value' => 0,
      'parameters' => [
         'unit' => 'unit.ampere'
      ]
   ],
   'battery_level_sensor' => [
      'instance_name' => 'battery_level_sensor',
      'description' => 'Уровень заряда',
      'capability' => 'float',
      'default_value' => 0,
      'parameters' => [
         'unit' => 'unit.percent'
      ]
   ],
   'co2_level_sensor' => [
      'instance_name' => 'co2_level_sensor',
      'description' => 'Углекислый газ',
      'capability' => 'float',
      'default_value' => 0,
      'parameters' => [
         'unit' => 'unit.ppm'
      ]
   ],
   'humidity_sensor' => [
      'instance_name' => 'humidity_sensor',
      'description' => 'Влажность',
      'capability' => 'float',
      'default_value' => 0,
      'parameters' => [
         'unit' => 'unit.percent'
      ]
   ],
   'illumination_sensor' => [
      'instance_name' => 'illumination_sensor',
      'description' => 'Освещенность',
      'capability' => 'float',
      'default_value' => 0,
      'parameters' => [
         'unit' => 'unit.illumination.lux'
      ]
   ],
   'power_sensor' => [
      'instance_name' => 'power_sensor',
      'description' => 'Мощность',
      'capability' => 'float',
      'default_value' => 0,
      'parameters' => [
         'unit' => 'unit.watt'
      ]
   ],
   'pressure_sensor' => [
      'instance_name' => 'pressure_sensor',
      'description' => 'Давление мм. рт. ст.',
      'capability' => 'float',
      'default_value' => 0,
      'parameters' => [
         'unit' => 'unit.pressure.mmhg'
      ]
   ],
   'temperature_sensor' => [
      'instance_name' => 'temperature_sensor',
      'description' => 'Температура',
      'capability' => 'float',
      'default_value' => 0,
      'parameters' => [
         'unit' => 'unit.temperature.celsius'
      ]
   ],
   'voltage_sensor' => [
      'instance_name' => 'voltage_sensor',
      'description' => 'Напряжение',
      'capability' => 'float',
      'default_value' => 0,
      'parameters' => [
         'unit' => 'unit.volt'
      ]
   ],
   'water_level_sensor' => [
      'instance_name' => 'water_level_sensor',
      'description' => 'Уровень воды',
      'capability' => 'float',
      'default_value' => 0,
      'parameters' => [
         'unit' => 'unit.percent'
      ]
   ],
   'open_sensor' => [
      'instance_name' => 'open_sensor',
      'description' => 'Датчик открытия/закрытия',
      'capability' => 'event',
      'default_value' => 0,
      'parameters' => [
         'events' => [
            ['value' => 'opened'],
            ['value' => 'closed'],
         ]
      ]
   ],
   'button_sensor' => [
      'instance_name' => 'button_sensor',
      'description' => 'Событие нажатия кнопки',
      'capability' => 'event',
      'default_value' => 0,
      'parameters' => [
         'events' => [
            ['value' => 'click'],
            ['value' => 'double_click'],
            ['value' => 'long_press'],
         ]
      ]
   ],
   'motion_sensor' => [
      'instance_name' => 'motion_sensor',
      'description' => 'Датчик движения',
      'capability' => 'event',
      'default_value' => 0,
      'parameters' => [
         'events' => [
            ['value' => 'detected'],
            ['value' => 'not_detected'],
         ]
      ]
   ],
   'smoke_sensor' => [
      'instance_name' => 'smoke_sensor',
      'description' => 'Датчик дыма',
      'capability' => 'event',
      'default_value' => 0,
      'parameters' => [
         'events' => [
            ['value' => 'detected'],
            ['value' => 'not_detected'],
            ['value' => 'high'],
         ]
      ]
   ],
   'gas_sensor' => [
      'instance_name' => 'gas_sensor',
      'description' => 'Датчик наличия газа в помещении',
      'capability' => 'event',
      'default_value' => 0,
      'parameters' => [
         'events' => [
            ['value' => 'detected'],
            ['value' => 'not_detected'],
            ['value' => 'high'],
         ]
      ]
   ],
   'water_leak_sensor' => [
      'instance_name' => 'water_leak_sensor',
      'description' => 'Датчик протечки',
      'capability' => 'event',
      'default_value' => 0,
      'parameters' => [
         'events' => [
            ['value' => 'dry'],
            ['value' => 'leak'],
         ]
      ]
   ],
];

// Определение доступных метрик для каждого типа устройства
$this->device_traits_mapping = [
    'light' => ['on', 'brightness', 'rgb', 'temperature_k'], // Свет: вкл/выкл, яркость, цвет, температура цвета
    'socket' => ['on'], // Розетка: только вкл/выкл
    'switch' => ['on'], // Переключатель: только вкл/выкл
    'sensor' => [
        'temperature_sensor', 'humidity_sensor', 'illumination_sensor', 
        'pressure_sensor', 'voltage_sensor', 'amperage_sensor', 
        'power_sensor', 'co2_level_sensor', 'battery_level_sensor', 
        'water_level_sensor', 'open_sensor', 'motion_sensor', 
        'smoke_sensor', 'gas_sensor', 'water_leak_sensor', 
        'button_sensor' // Датчики: различные типы сенсоров
    ],
    'openable' => ['on', 'open', 'pause'], // Открываемые: вкл/выкл, открытие, пауза
    'openable.curtain' => ['on', 'open', 'pause'], // Шторы: аналогично открываемым
    'thermostat' => ['on', 'temperature', 'thermostat', 'keep_warm'], // Термостаты: вкл/выкл, темп, режим, поддержание тепла
    'thermostat.ac' => ['on', 'temperature', 'thermostat', 'fan_speed', 'mute'], // Кондиционеры: + вентиляция, без звука
    'media_device.tv' => ['on', 'input_source', 'volume', 'mute', 'channel', 'backlight'], // ТВ: + источники, громкость, каналы, подсветка
    'media_device.tv_box' => ['on', 'input_source', 'volume', 'mute', 'channel', 'backlight'], // ТВ-приставка: аналогично ТВ
    'media_device.receiver' => ['on', 'input_source', 'volume', 'mute', 'backlight'], // Ресивер: без канала
    'vacuum_cleaner' => ['on', 'fan_speed', 'pause'], // Пылесос: вентиляция, пауза
    'cooking.kettle' => ['on', 'temperature'], // Чайник: вкл/выкл, температура
    'other' => array_keys($this->devices_instance), // Остальные: все доступные метрики (определить подмножество)
    'media_device' => array_keys($this->devices_instance), // Аудио/видео: все (или подмножество)
];

?>
