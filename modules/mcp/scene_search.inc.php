<?php
/*
* Список сцен/групп.
*/

$res = SQLSelect("SELECT * FROM mcp_scenes ORDER BY TITLE");

if (isset($res[0]['ID'])) {
    $device_titles = [];
    $devices = SQLSelect("SELECT ID, TITLE, ROOM FROM mcp_devices");
    foreach ($devices as $device) {
        $device_titles[$device['ID']] = trim($device['TITLE'] . ($device['ROOM'] != '' ? ' (' . $device['ROOM'] . ')' : ''));
    }

    $total = count($res);
    for ($i = 0; $i < $total; $i++) {
        $actions = json_decode($res[$i]['ACTIONS'], true);
        $res[$i]['ACTIONS_COUNT'] = is_array($actions) ? count($actions) : 0;

        $preview = [];
        if (is_array($actions)) {
            foreach ($actions as $action) {
                $device_title = isset($device_titles[$action['device_id']]) ? $device_titles[$action['device_id']] : ('#' . $action['device_id']);
                $state = ($action['action'] == 'off') ? 'выкл' : 'вкл';
                if (isset($action['brightness']) && $action['brightness'] !== '' && $action['brightness'] !== null) {
                    $state .= ' ' . $action['brightness'] . '%';
                }
                $preview[] = $device_title . ': ' . $state;
            }
        }
        $res[$i]['ACTIONS_PREVIEW'] = implode(', ', $preview);
        $res[$i]['ENABLED_TITLE'] = $res[$i]['ENABLED'] ? 'да' : 'нет';
    }
    $out['RESULT'] = $res;
}
