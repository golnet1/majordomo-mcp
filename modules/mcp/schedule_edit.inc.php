<?php
/*
* Планировщик: добавить или изменить задание.
*
* ACTION_DATA (JSON): {"type":"on|off","linked_object":"...","linked_property":"...","brightness":30}
*/

$id = (int)$id;

$rec = [];
if ($id > 0) {
    $rec = SQLSelectOne("SELECT * FROM mcp_schedule WHERE ID=" . $id);
    if (!isset($rec['ID'])) {
        return;
    }
}
$out['ID'] = $id;

// Список устройств для выбора.
$devices = SQLSelect("SELECT ID, TITLE, ROOM, TYPE, TRAITS FROM mcp_devices ORDER BY ROOM, TITLE");
foreach ($devices as $k => $device) {
    $traits = json_decode($device['TRAITS'], true);
    $has_brightness = false;
    if (is_array($traits)) {
        foreach ($traits as $trait) {
            if (isset($trait['type']) && $trait['type'] === 'brightness') {
                $has_brightness = true;
                break;
            }
        }
    }
    $devices[$k]['HAS_BRIGHTNESS'] = $has_brightness ? 1 : 0;
    $devices[$k]['TITLE_FULL'] = trim($device['TITLE'] . ($device['ROOM'] != '' ? ' (' . $device['ROOM'] . ')' : ''));
}
$out['DEVICES'] = $devices;
// htmlspecialchars — потому что JSON вставляется в HTML-атрибут, а JS сделает JSON.parse.
$out['DEVICES_JSON'] = htmlspecialchars(json_encode($devices, JSON_UNESCAPED_UNICODE), ENT_QUOTES);

// Сохранение.
if ($this->mode == 'update') {
    $ok = 1;

    $rec['TITLE'] = gr('title');
    $rec['ENABLED'] = (int)gr('enabled');
    $rec['SCHEDULE_TIME'] = trim(gr('schedule_time'));
    $rec['ACTION_TYPE'] = 'device';

    // Дни недели: либо «одноразово», либо набор дней.
    $days = [];
    if (isset($_POST['days']) && is_array($_POST['days'])) {
        $days = $_POST['days'];
    }
    if (gr('once') == '1') {
        $days = ['once'];
    }
    $normalized_days = $this->normalizeScheduleDays($days);
    $rec['SCHEDULE_DAYS'] = json_encode($normalized_days, JSON_UNESCAPED_UNICODE);
    $is_once = (count($normalized_days) === 1 && $normalized_days[0] === 'once');

    // Устройство и действие.
    $device_title = trim(gr('device'));
    $rec['ACTION_TARGET'] = $device_title;
    $action = (gr('action') == 'off') ? 'off' : 'on';
    $rec['ACTION_STATE'] = ($action == 'off') ? 'выключить' : 'включить';

    if ($device_title == '') {
        $out['ERR_DEVICE'] = 1;
        $ok = 0;
    }
    if ($rec['SCHEDULE_TIME'] == '') {
        $out['ERR_TIME'] = 1;
        $ok = 0;
    }

    // ACTION_DATA — разрешаем канал управления по выбранному устройству.
    $data = ['type' => $action];
    $device = SQLSelectOne("SELECT * FROM mcp_devices WHERE TITLE='" . DBSafe($device_title) . "'");
    if ($device) {
        list($obj, $prop) = $this->scheduleTraitChannel($device, ['on']);
        if ($obj !== null && $prop !== null) {
            $data['linked_object'] = $obj;
            $data['linked_property'] = $prop;
        }
    }
    $brightness = gr('brightness');
    if ($brightness !== '' && $brightness !== null) {
        $data['brightness'] = max(0, min(100, (int)$brightness));
    }
    $rec['ACTION_DATA'] = json_encode($data, JSON_UNESCAPED_UNICODE);

    // Дата для одноразового задания.
    if ($is_once) {
        $date = trim(gr('schedule_date'));
        if ($date === '') {
            // Ближайшее наступление указанного времени.
            if (preg_match('/^(\d{1,2}):(\d{2})$/', $rec['SCHEDULE_TIME'], $m)) {
                $target = mktime((int)$m[1], (int)$m[2], 0);
                if ($target <= time()) {
                    $target += 86400;
                }
                $date = date('Y-m-d', $target);
            }
        }
        $rec['SCHEDULE_DATE'] = $date;
    } else {
        $rec['SCHEDULE_DATE'] = null;
    }

    if ($ok) {
        if ($id > 0) {
            $rec['ID'] = $id;
            SQLUpdate('mcp_schedule', $rec);
        } else {
            $rec['TASK_ID'] = 'admin_' . date('Ymd_His') . '_' . substr(md5($device_title . microtime()), 0, 6);
            $rec['ID'] = SQLInsert('mcp_schedule', $rec);
        }
        $out['OK'] = 1;
        $this->redirect('?view_mode=search_mcpSchedule');
    } else {
        $out['ERR'] = 1;
    }
}

// Значения по умолчанию для новой записи.
if (!isset($rec['SCHEDULE_DAYS']) || $rec['SCHEDULE_DAYS'] == '') {
    $rec['SCHEDULE_DAYS'] = json_encode(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']);
}
if (!isset($rec['ENABLED']) || $rec['ENABLED'] === '') {
    $rec['ENABLED'] = 1;
}

$days = $this->normalizeScheduleDays($rec['SCHEDULE_DAYS']);
$rec['DAY_ONCE'] = (count($days) === 1 && $days[0] === 'once') ? 1 : 0;
foreach (['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as $day) {
    $rec['DAY_' . strtoupper($day)] = in_array($day, $days, true) ? 1 : 0;
}

// Состояние действия и яркость — из ACTION_DATA.
$data = json_decode(isset($rec['ACTION_DATA']) ? $rec['ACTION_DATA'] : '', true);
if (!is_array($data)) {
    $data = [];
}
$rec['ACTION_ON'] = (isset($data['type']) && $data['type'] == 'off') ? 0 : 1;
$rec['ACTION_BRIGHTNESS'] = isset($data['brightness']) ? $data['brightness'] : '';
if (!isset($rec['ACTION_TARGET'])) {
    $rec['ACTION_TARGET'] = '';
}
if (!isset($rec['SCHEDULE_TIME'])) {
    $rec['SCHEDULE_TIME'] = '';
}
if (!isset($rec['SCHEDULE_DATE'])) {
    $rec['SCHEDULE_DATE'] = '';
}
if (!isset($rec['TITLE'])) {
    $rec['TITLE'] = '';
}

if (is_array($rec)) {
    foreach ($rec as $k => $v) {
        if (!is_array($v)) {
            $rec[$k] = htmlspecialchars($v);
        }
    }
}

outHash($rec, $out);
