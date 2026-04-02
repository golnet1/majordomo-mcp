<?php
/*
* @version 0.1 (wizard)
*/


$res = SQLSelect("SELECT * FROM mcp_devices ORDER BY ROOM,TITLE");
$loc_title = '';
if ($res[0]['ID']) {
    $total = count($res);
    for($i = 0; $i < $total; $i++) {
        $res[$i]['ICON'] = strtolower($res[$i]['TYPE']);
        $res[$i]['TYPE_TITLE'] = $this->devices_type[$res[$i]['TYPE']]['description'];
        $res[$i]['VIEW_STYLE'] = $this->config['VIEW_STYLE'];
        if ($res[$i]['ROOM'] != $loc_title) {
            $res[$i]['NEW_ROOM'] = 1;
            $loc_title = $res[$i]['ROOM'];
        }
        $res[$i]['LAST_DEV'] = 0;
        if (isset($res[$i]['NEW_ROOM'])) {
            if ($i == $total-1) {
                $res[$i]['LAST_DEV'] = 1;
            }
            if ($i > 0) {
                $res[$i-1]['LAST_DEV'] = 1;
            }
        } else if (!isset($res[$i]['NEW_ROOM']) && ($i == $total-1)) {
            $res[$i]['LAST_DEV'] = 1;
        }
        $traits = json_decode($res[$i]['TRAITS'], true);
        if (is_array($traits) && count($traits) > 0) {
            $res[$i]['TRAITS_LIST'] = "";
            foreach ($traits as $trait) {
                $res[$i]['TRAITS_LIST'] .= $trait['type'] . '<br>';
            }
        }
    }
    $out['RESULT'] = $res;
}