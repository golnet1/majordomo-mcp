<?php
$out['DEVICES_TYPE'] = array_values($this->devices_type);
$out['LOCATIONS'] = SQLSelect('SELECT ID, TITLE FROM locations ORDER BY TITLE');

if ($this->mode == 'addnew') {
    $ok = 1;

    $rec['TITLE'] = gr('title');
    if ($rec['TITLE'] == '') {
        $out['ERR_TITLE'] = 1;
        $ok = 0;
    }
		 
    $rec['ALIACE'] = gr('aliace');
    if ($rec['ALIACE'] == '') {
        $out['ERR_ALIACE'] = 1;
        $ok = 0;
    }

    $rec['TYPE'] = gr('type');
    if ($rec['TYPE'] == '') {
        $out['ERR_TYPE'] = 1;
        $ok = 0;
    }

    $rec['ROOM'] = gr('location');

    if ($ok) {
        $new_rec = 1;
        $rec['ID'] = SQLInsert('mcp_devices', $rec);
        $out['OK'] = 1;
    } else {
        $out['ERR'] = 1;
    }

    if (is_array($rec)) {
        foreach($rec as $k=>$v) {
            if (!is_array($v)) {
                $rec[$k] = htmlspecialchars($v);
            }
        }
    }

    if ($ok) {
        $this->redirect('?');
    }
}
outHash($rec, $out);