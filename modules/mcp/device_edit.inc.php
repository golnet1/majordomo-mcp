<?php
// ID, TITLE, TYPE, ROOM, TRAITS (json), CONFIG (json)
$rec = SQLSelectOne("SELECT * FROM mcp_devices WHERE ID='{$id}'");

// Поддерживаемые типы устройств.
$out['DEVICES_TYPE'] = array_values($this->devices_type);

      // Поддерживаемые метрики (возможности) устройств.
$out['DEVICES_INSTANCE'] = array_values($this->devices_instance);
$out['DEVICES_INSTANCE_JSON'] = json_encode($this->devices_instance, JSON_UNESCAPED_UNICODE);
$out['DEVICE_TRAITS_MAPPING_JSON'] = json_encode($this->device_traits_mapping, JSON_UNESCAPED_UNICODE);

// Список местоположений (комнат) в системе.
$out['LOCATIONS'] = SQLSelect('SELECT ID, TITLE FROM locations ORDER BY TITLE');

// Список объектов в системе.
$objs = SQLSelect('SELECT TITLE, DESCRIPTION FROM objects ORDER BY TITLE'); // CLASS_ID
$out['OBJECTS'] = json_encode($objs, JSON_UNESCAPED_UNICODE);

// Сохранение конфигурации устройства.
if ($this->mode == 'update') {

    $ok = 1;

    // Название устройства (обязательное поле).
    $rec['TITLE'] = gr('title');
    if ($rec['TITLE'] == '') {
        $out['ERR_TITLE'] = 1;
        $ok = 0;
    }
		 
    // Псевдоним устройства (обязательное поле) несколько через запятую или точку с запятой.
    $rec['ALIACE'] = gr('aliace');
    if ($rec['ALIACE'] == '') {
        $out['ERR_ALIACE'] = 1;
        $ok = 0;
    }

    // Тип устройства (обязательное поле).
    $rec['TYPE'] = gr('type');
    if ($rec['TYPE'] == '') {
        $out['ERR_TYPE'] = 1;
        $ok = 0;
    }

    // Местоположение устройства (опционально).
    $rec['ROOM'] = gr('location');

    // Описание устройства (опционально).
    $rec['DESCRIPTION'] = gr('description');

    // Производитель устройства (опционально).
    $rec['MANUFACTURER'] = gr('manufacturer');

    // Модель устройства (опционально).
    $rec['MODEL'] = gr('model');

    // Версия ПО устройства (опционально).
    $rec['SW_VERSION'] = gr('sw_version');

    // Версия АО устройства (опционально).
    $rec['HW_VERSION'] = gr('hw_version');

    // Метрики (в т.ч. привязанные к ним объекты и свойства) устройства (обязательное поле).
    // Старые (в формате массива).
    $old_dev_traits = json_decode($rec['TRAITS'], true);
    // Новые (в формате json).
    $rec['TRAITS'] = gr('traits_json');
    // Новые (массив).
    $new_dev_traits = json_decode($rec['TRAITS'], true);
    if ($rec['TRAITS'] == '' || count($new_dev_traits) == 0) {
        $out['ERR_TRAITS'] = 1;
        $ok = 0;
    }

    // Конфигурация умений.
    $devices_instance = json_decode(gr('instance_json'), true);

    // Если обязательные поля заполнены, то сохраняем конфигурацию устройства.
    if ($ok) {
        if ($rec['ID']) {
            // Собираем JSON-конфиг устройства согласно формату API Yandex Home.
            $traits = [];
            $properties = [];
            if (is_array($new_dev_traits)) {
                foreach ($new_dev_traits as $trait) {
                    $parameters = [];
					 
                    //Отправка в Яндекс
                    $trait['reportable'] = ($trait['reportable'] != null) ?? false;

                    if (($this->devices_instance[$trait['type']]['capability'] == 'float') || ($this->devices_instance[$trait['type']]['capability'] == 'event')) {
                        $trait_type = PREFIX_PROPERTIES . $this->devices_instance[$trait['type']]['capability'];
                    } else {
                        $trait_type = PREFIX_CAPABILITIES . $this->devices_instance[$trait['type']]['capability'];
                    }

                    if (($this->devices_instance[$trait['type']]['capability'] == 'float') || ($this->devices_instance[$trait['type']]['capability'] == 'event')) {
                        $instance_name = str_replace('_sensor', '', $trait['type']);
                    } else {
                        $instance_name = $trait['type'];
                    }

                    if (isset($devices_instance[$trait['type']]['parameters'])) {
                        $parameters = $devices_instance[$trait['type']]['parameters'];
                        if ($trait['type'] != 'rgb' && $trait['type'] != 'temperature_k') {
                            $parameters['instance'] = $instance_name;
                        }
                    } else {
                        $parameters['instance'] = $instance_name;
                    }
                    $check = false;
                    foreach ($traits as $key => $item) {
                        if ($item['type'] == $trait_type) {
                            $check = $key;
                            break;
                        }
                    }
                    if ($check && $trait_type == PREFIX_CAPABILITIES.'color_setting') {
                        $traits[$check]['parameters'] = array_merge ($traits[$check]['parameters'], $parameters);
                    } else {
                        if (isset($this->devices_instance[$trait['type']]['retrievable'])) {
                            $retrievable = $this->devices_instance[$trait['type']]['retrievable'];
                        } else {
                            $retrievable = true;
                        }
						
                        if (($this->devices_instance[$trait['type']]['capability'] == 'float') || ($this->devices_instance[$trait['type']]['capability'] == 'event')) {
                            $properties[] = [
                                'type' => $trait_type,
                                'parameters' => $parameters,
                                'retrievable' => $retrievable,
                                'reportable' => $trait['reportable']
                            ];
                        } else {
                            $traits[] = [
                                'type' => $trait_type,
                                'parameters' => $parameters,
                                'retrievable' => $retrievable,
                                'reportable' => $trait['reportable']
                            ];
                        }
                    }
                }
            }

            $rec['CONFIG'] =  json_encode([
                'id' => $rec['ID'],
                'name' => $rec['TITLE'],
				'aliace' => $rec['ALIACE'],
                'type' => PREFIX_TYPES . $rec['TYPE'],
                'room' => $rec['ROOM'],
                'description' => $rec['DESCRIPTION'],
                'capabilities' => $traits,
                'properties' => $properties,
                'device_info' => [
                    'manufacturer' => $rec['MANUFACTURER'],
                    'model' => $rec['MODEL'] . ' | MajorDoMo',
                    'hw_version' => $rec['HW_VERSION'],
                    'sw_version' => $rec['SW_VERSION']
                ]
            ], JSON_UNESCAPED_UNICODE);

            // Обрабатываем набор метрик и привязанные к ним объекты и свойства.
            if (is_array($new_dev_traits)) {
                if (is_array($old_dev_traits)) {
                    // Если удалили метрику, у которой были привязанные объект и свойство, то удаляем линк.
                    $del_dev_traits = array_diff_assoc($old_dev_traits, $new_dev_traits);
                    if (!empty($del_dev_traits)) {
                        foreach ($del_dev_traits as $trait) {
                            $linked_object = $trait['linked_object'];
                            $linked_property = $trait['linked_property'];
                            if ($linked_object != '' && $linked_property != '') {
                                removeLinkedProperty($linked_object, $linked_property, $this->name);
                                $this->WriteLog("removeLinkedProperty for $linked_object and $linked_property");
                            }
                        }
                    }
                }

                foreach ($new_dev_traits as $trait) {
                    // Новые объект и свойство метрики.
                    $linked_object = $trait['linked_object'];
                    $linked_property = $trait['linked_property'];

                    // Предыдущие объект и свойство метрики.
                    if (isset($old_dev_traits[$trait['type']])) {
                        $old_linked_object = $old_dev_traits[$trait['type']]['linked_object'];
                        $old_linked_property = $old_dev_traits[$trait['type']]['linked_property'];
                    } else {
                        $old_linked_object = '';
                        $old_linked_property = '';
                    }

                    // Если юзер удалил привязанное свойство, но забыл про объект, то очищаем его.
                    if ($linked_object != '' && $linked_property == '') {
                        $linked_object = '';
                        $new_dev_traits[$trait['type']]['linked_object'] = '';
                    }

                    // Если юзер удалил только привязанный объект, то свойство тоже очищаем.
                    if ($linked_object == '' && $linked_property != '') {
                        $linked_property = '';
                        $new_dev_traits[$trait['type']]['linked_property'] = '';
                    }

                    // Если предыдущие привязанные объект и свойство не пустые и не совпадают с новыми, то удаляем линк.
                    if ($old_linked_object !='' && $old_linked_property != '' && ($linked_object.$linked_property != $old_linked_object.$old_linked_property)) {
                        removeLinkedProperty($old_linked_object, $old_linked_property, $this->name);
                        $this->WriteLog("removeLinkedProperty for $old_linked_object and $old_linked_property");
                    }

                    // Если поля привязанного объекта и свойства не пустые  и не совпадают с предыдущими, то проставляем линк.
                    if ($linked_object != '' && $linked_property != '' && ($linked_object.$linked_property != $old_linked_object.$old_linked_property)) {
                        addLinkedProperty($linked_object, $linked_property, $this->name);
                        $this->WriteLog("addLinkedProperty for $linked_object and $linked_property");
                    }
                }
                $rec['TRAITS'] = json_encode($new_dev_traits, JSON_UNESCAPED_UNICODE);
            }
            // Обновляем запись об устройстве в БД.
            SQLUpdate('mcp_devices', $rec);
        }
            $out['OK'] = 1;
        } else {
            $out['ERR'] = 1;
        }
    }
outHash($rec, $out);
