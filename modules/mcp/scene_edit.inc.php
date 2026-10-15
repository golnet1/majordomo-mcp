<?php
/*
* Сцена/группа: добавить или изменить.
*
* Формат ACTIONS (JSON-массив):
*   [{"device_id": 3, "action": "on|off", "brightness": 30|null}, ...]
*/

$id = (int)$id;

$rec = [];
if ($id > 0) {
    $rec = SQLSelectOne("SELECT * FROM mcp_scenes WHERE ID=" . $id);
    if (!isset($rec['ID'])) {
        return;
    }
}
$out['ID'] = $id;

// Список устройств для выбора в действиях сцены.
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
    $devices[$k]['TYPE_TITLE'] = isset($this->devices_type[$device['TYPE']]['description'])
        ? $this->devices_type[$device['TYPE']]['description'] : $device['TYPE'];
}
$out['DEVICES'] = $devices;
// htmlspecialchars — потому что JSON вставляется в HTML-атрибут (value="..."),
// браузер вернёт исходные кавычки, а JS сделает JSON.parse.
$out['DEVICES_JSON'] = htmlspecialchars(json_encode($devices, JSON_UNESCAPED_UNICODE), ENT_QUOTES);

// Сохранение.
if ($this->mode == 'update') {
    $ok = 1;

    $rec['TITLE'] = gr('title');
    if ($rec['TITLE'] == '') {
        $out['ERR_TITLE'] = 1;
        $ok = 0;
    }

    $rec['ALIACE'] = gr('aliace');
    $rec['ENABLED'] = (int)gr('enabled');

    // Действия приходят JSON-строкой, которую собирает JS в шаблоне.
    $actions_raw = gr('actions_json');
    $actions = json_decode($actions_raw, true);
    $clean_actions = [];
    if (is_array($actions)) {
        foreach ($actions as $action) {
            if (!isset($action['device_id'])) {
                continue;
            }
            $device_id = (int)$action['device_id'];
            if ($device_id <= 0) {
                continue;
            }
            $item = [
                'device_id' => $device_id,
                'action' => (isset($action['action']) && $action['action'] == 'off') ? 'off' : 'on',
            ];
            if (isset($action['brightness']) && $action['brightness'] !== '' && $action['brightness'] !== null) {
                $item['brightness'] = max(0, min(100, (int)$action['brightness']));
            }
            $clean_actions[] = $item;
        }
    }
    $rec['ACTIONS'] = json_encode($clean_actions, JSON_UNESCAPED_UNICODE);

    if ($ok) {
        if ($id > 0) {
            $rec['ID'] = $id;
            SQLUpdate('mcp_scenes', $rec);
        } else {
            $rec['ID'] = SQLInsert('mcp_scenes', $rec);
        }
        $out['OK'] = 1;
        $this->redirect('?view_mode=search_mcpScene');
    } else {
        $out['ERR'] = 1;
    }
}

if (!isset($rec['ACTIONS']) || $rec['ACTIONS'] == '') {
    $rec['ACTIONS'] = '[]';
}

$raw_actions = $rec['ACTIONS'];

if (is_array($rec)) {
    foreach ($rec as $k => $v) {
        if (!is_array($v)) {
            $rec[$k] = htmlspecialchars($v);
        }
    }
}
$out['ACTIONS_JSON'] = htmlspecialchars($raw_actions, ENT_QUOTES);

outHash($rec, $out);
