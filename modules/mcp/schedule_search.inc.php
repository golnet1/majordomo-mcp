<?php
/*
* Список заданий планировщика.
*/

$this->ensureScheduleTable();

$res = SQLSelect("SELECT * FROM mcp_schedule ORDER BY ENABLED DESC, SCHEDULE_TIME, ID");

if (isset($res[0]['ID'])) {
    $day_ru = [
        'mon' => 'Пн', 'tue' => 'Вт', 'wed' => 'Ср', 'thu' => 'Чт',
        'fri' => 'Пт', 'sat' => 'Сб', 'sun' => 'Вс',
    ];
    $on_words = ['включи', 'включить', 'вкл', 'on', '1', 'да', 'true'];

    $total = count($res);
    for ($i = 0; $i < $total; $i++) {
        $days = $this->normalizeScheduleDays($res[$i]['SCHEDULE_DAYS']);
        $once = (count($days) === 1 && $days[0] === 'once');

        if ($once) {
            $when = 'одноразово';
            if (!empty($res[$i]['SCHEDULE_DATE']) && $res[$i]['SCHEDULE_DATE'] !== '0000-00-00') {
                $when .= ' (' . $res[$i]['SCHEDULE_DATE'] . ')';
            }
        } elseif (count($days) === 7) {
            $when = 'каждый день';
        } elseif (count($days) === 5 && !in_array('sat', $days, true) && !in_array('sun', $days, true)) {
            $when = 'по будням';
        } elseif (count($days) === 2 && in_array('sat', $days, true) && in_array('sun', $days, true)) {
            $when = 'по выходным';
        } else {
            $parts = [];
            foreach ($days as $day) {
                $parts[] = isset($day_ru[$day]) ? $day_ru[$day] : $day;
            }
            $when = implode(', ', $parts);
        }
        $res[$i]['DAYS_TITLE'] = $when;

        $state = mb_strtolower(trim((string)$res[$i]['ACTION_STATE']), 'UTF-8');
        $is_on = in_array($state, $on_words, true);
        $action_title = $is_on ? 'включить' : 'выключить';
        $data = json_decode($res[$i]['ACTION_DATA'], true);
        if (is_array($data) && isset($data['brightness']) && $data['brightness'] !== '') {
            $action_title .= ' ' . (int)$data['brightness'] . '%';
        }
        $res[$i]['ACTION_TITLE'] = $action_title;
        $res[$i]['DEVICE_TITLE'] = $res[$i]['TITLE'] != '' ? $res[$i]['TITLE'] : $res[$i]['ACTION_TARGET'];

        $res[$i]['ENABLED_TITLE'] = $res[$i]['ENABLED'] ? 'да' : 'нет';
        $res[$i]['TOGGLE_TITLE'] = $res[$i]['ENABLED'] ? 'Отключить' : 'Включить';
        $res[$i]['TOGGLE_ICON'] = $res[$i]['ENABLED'] ? 'glyphicon-pause' : 'glyphicon-play';
    }
    $out['RESULT'] = $res;
}
