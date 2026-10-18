<?php
/**
* MCP 
* @package project
* @author <Oleg M>
* @copyright 2026 Malov Oleg aka golnet <golnet@mail.ru> (c)
* @version 0.1 (11:01:35 [Jan 27, 2026])
*/

const PREFIX_CAPABILITIES = 'devices.capabilities.';
const PREFIX_PROPERTIES = 'devices.properties.';
const PREFIX_TYPES = 'devices.types.';
const API_VERSION = '1.0';

class mcp extends module {
 /**
 * MCP
 *
 * Module class constructor
 *
 * @access private
 */
 
 function __construct() {
     $this->name = "mcp";
     $this->title = "MCP";
     $this->module_category = "<#LANG_SECTION_DEVICES#>";
	 
	 // Инициализация путей к виртуальному окружению
    $this->venv_path = dirname(__FILE__) . '/lib/.venv';
    $this->python_bin = $this->getVenvBinary('python3', 'python');
    $this->pip_bin = $this->getVenvBinary('pip3', 'pip');
	 
     $this->checkInstalled();
  
     $this->getConfig();
	 $this->python = ($this->config['CHECK_PYTHON'] == 1) ? true : false;
     $this->debug = ($this->config['LOG_DEBMES'] == 1) ? true : false;
     $this->debug_ping = ($this->config['LOG_PING'] == 1) ? true : false;

    $this->mcpPipeProcessName = 'mcp_pipe.py'; // маркер процесса для поиска
    // Основной (встроенный) MCP-сервер модуля — его нельзя удалить из админки:
    // без него мост не поднимается и LLM не видит инструменты.
    $this->mainMcpServerName = 'mcp';
     require ('structure.inc.php');
 }

 function isWindowsPlatform() {
    return strtoupper(substr(PHP_OS, 0, 3)) === 'WIN';
 }

 function getVenvBinary($unixName, $windowsName) {
    if ($this->isWindowsPlatform()) {
        return $this->venv_path . '/Scripts/' . $windowsName . '.exe';
    }
    return $this->venv_path . '/bin/' . $unixName;
 }

 function getSystemPythonCommand() {
    if ($this->isWindowsPlatform()) {
        return 'where python';
    }
    return 'which python3';
 }

 /**
  * Кодирует скрипт PowerShell для параметра -EncodedCommand (UTF-16LE + base64).
  * Позволяет передавать пути/значения как данные, без экранирования cmd.exe.
  */
 function encodePowerShellCommand($script) {
     $utf16le = '';
     if (function_exists('iconv')) {
         $converted = @iconv('UTF-8', 'UTF-16LE', $script);
         if ($converted !== false) {
             $utf16le = $converted;
         }
     }
     if ($utf16le === '' && function_exists('mb_convert_encoding')) {
         $converted = @mb_convert_encoding($script, 'UTF-16LE', 'UTF-8');
         if ($converted !== false) {
             $utf16le = $converted;
         }
     }
     if ($utf16le === '') {
         // Резервный вариант для ASCII-скриптов (mbstring/iconv недоступны)
         for ($i = 0, $len = strlen($script); $i < $len; $i++) {
             $utf16le .= $script[$i] . "\x00";
         }
     }
     return base64_encode($utf16le);
 }

 /**
  * Поиск PID процессов по маркеру командной строки в Windows.
  * Использует PowerShell (Get-CimInstance) — wmic устарел и удалён в новых Windows.
  * Отбор только по процессам python*, поэтому сам запрос (powershell.exe) не попадает.
  */
 function getWindowsPidsByName($processName, $num = 0) {
     $pids = [];
     $pattern = '*' . str_replace("'", "''", $processName) . '*';
     $psScript = "\$ErrorActionPreference = 'SilentlyContinue'; \$ProgressPreference = 'SilentlyContinue'; "
         . "Get-CimInstance Win32_Process | Where-Object { \$_.Name -like 'python*' -and \$_.CommandLine -like '" . $pattern . "' } "
         . "| Select-Object -ExpandProperty ProcessId";
     $encoded = $this->encodePowerShellCommand($psScript);
     $cmd = 'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' . $encoded;
     $raw = [];
     $return_var = -1;
     exec($cmd, $raw, $return_var);
     foreach ($raw as $line) {
         if (preg_match('/(\d+)/', trim($line), $m)) {
             $pid = (int)$m[1];
             if ($pid > 0 && $pid !== getmypid()) {
                 $pids[] = $pid;
             }
         }
     }

     // Резервный вариант через wmic (старые сборки Windows)
     if (empty($pids)) {
         $escaped = str_replace("'", "''", $processName);
         $cmd = 'wmic process where "CommandLine like \'%' . $escaped . '%\'" get ProcessId /value 2>NUL';
         $raw = [];
         exec($cmd, $raw, $return_var);
         foreach ($raw as $line) {
             if (preg_match('/ProcessId=(\d+)/i', trim($line), $m)) {
                 $pid = (int)$m[1];
                 if ($pid > 0 && $pid !== getmypid()) {
                     $pids[] = $pid;
                 }
             }
         }
     }

     $pids = array_values(array_unique($pids));
     if (empty($pids)) {
         return null;
     }
     if ($num == 0) {
         return (int)$pids[0];
     }
     return $pids;
 }

 /**
 * saveParams
 *
 * Saving module parameters
 *
 * @access public
 */
 function saveParams($data=1) {
     $p=array();
     if (IsSet($this->id)) {
         $p["id"]=$this->id;
     }
     if (IsSet($this->view_mode)) {
         $p["view_mode"]=$this->view_mode;
     }
     if (IsSet($this->edit_mode)) {
         $p["edit_mode"]=$this->edit_mode;
     }
     if (IsSet($this->tab)) {
         $p["tab"]=$this->tab;
     }
     return parent::saveParams($p);
 }
 /**
 * getParams
 *
 * Getting module parameters from query string
 *
 * @access public
 */
 function getParams() {
     global $id;
     global $mode;
     global $view_mode;
     global $edit_mode;
     global $tab;
     if (isset($id)) {
         $this->id=$id;
     }
     if (isset($mode)) {
         $this->mode=$mode;
     }
     if (isset($view_mode)) {
         $this->view_mode=$view_mode;
     }
     if (isset($edit_mode)) {
         $this->edit_mode=$edit_mode;
     }
     if (isset($tab)) {
         $this->tab=$tab;
     }
 }
 /**
 * Run
 *
 * Description
 *
 * @access public
 */
 function run() {
     global $session;
     $out=array();
     $out['VERSION']='2.01.24';
     $out['DATA_V']='18.10.26';
     if ($this->action=='admin') {
         $this->admin($out);
     } else {
         $this->usual($out);
     }
     if (IsSet($this->owner->action)) {
         $out['PARENT_ACTION']=$this->owner->action;
     }
     if (IsSet($this->owner->name)) {
         $out['PARENT_NAME']=$this->owner->name;
     }
     $out['VIEW_MODE']=$this->view_mode;
     $out['EDIT_MODE']=$this->edit_mode;
     $out['MODE']=$this->mode;
     $out['ACTION']=$this->action;
     $out['TAB']=$this->tab;
     $this->data=$out;
     $p=new parser(DIR_TEMPLATES.$this->name."/".$this->name.".html", $this->data, $this);
     $this->result=$p->result;
 }
 /**
 * BackEnd
 *
 * Module backend
 *
 * @access public
 */
 function admin(&$out) {
     $this->getConfig();
 
    $out['MCP_ENDPOINT'] =  $this->config['MCP_ENDPOINT'];
     $out['VIEW_STYLE'] =  $this->config['VIEW_STYLE'];
	 $out['CHECK_PYTHON'] =  $this->config['CHECK_PYTHON'];
     $out['LOG_DEBMES'] =  $this->config['LOG_DEBMES'];
     $out['LOG_PING']   =  $this->config['LOG_PING'];
     $out['BRIGHTNESS_NO_DIMMER'] = isset($this->config['BRIGHTNESS_NO_DIMMER']) ? $this->config['BRIGHTNESS_NO_DIMMER'] : 'error';
     // Внешний HTTP-мост для управления колонками (свой xiaozhi-сервер/шлюз).
     // Пусто — прямое управление недоступно, команды идут в очередь к колонке.
     $out['SPEAKER_API_URL'] = isset($this->config['SPEAKER_API_URL']) ? $this->config['SPEAKER_API_URL'] : '';
     $out['SPEAKER_API_TOKEN'] = isset($this->config['SPEAKER_API_TOKEN']) ? $this->config['SPEAKER_API_TOKEN'] : '';
     // Человекочитаемые значения для служебной информации (в настройках хранятся коды).
     $out['BRIGHTNESS_NO_DIMMER_LABEL'] = ($out['BRIGHTNESS_NO_DIMMER'] === 'on')
         ? 'Включить на 100% и предупредить'
         : 'Сообщить, что устройство не поддерживает яркость';
     $view_styles = [
         'table'        => 'Таблица',
         'table_group'  => 'Таблица с группировкой',
         'panels'       => 'Панели',
         'panels_group' => 'Панели с группировкой',
     ];
     $out['VIEW_STYLE_LABEL'] = isset($view_styles[$out['VIEW_STYLE']])
         ? $view_styles[$out['VIEW_STYLE']]
         : ($out['VIEW_STYLE'] !== '' ? $out['VIEW_STYLE'] : 'Панели с группировкой');
     $out['LOG_DEBMES_LABEL'] = ($out['LOG_DEBMES'] === '1') ? 'включена' : 'выключена';
     $out['LOG_PING_LABEL']   = ($out['LOG_PING'] === '1') ? 'включено' : 'выключено';
 
     if ($this->view_mode=='update_settings') {

        $this->config['MCP_ENDPOINT'] = gr('mcp_endpoint');
        $this->config['VIEW_STYLE'] = gr('view_style');
		$this->config['CHECK_PYTHON'] = gr('check_python');
        $this->config['LOG_DEBMES'] = gr('log_debmes');
        $this->config['LOG_PING']   = gr('log_ping');
        $this->config['BRIGHTNESS_NO_DIMMER'] = gr('brightness_no_dimmer');
        $this->config['SPEAKER_API_URL'] = gr('speaker_api_url');
        $this->config['SPEAKER_API_TOKEN'] = gr('speaker_api_token');
		
		setGlobal('cycle_mcpExit', true);

        $this->saveConfig();
		
		$current_pid = $this->getPidByName($this->mcpPipeProcessName);
        if ($current_pid !== null) {
            $this->stopMcpPipeProcess();
        }
		
        $this->redirect("?");
     }

     // Автоисправление окружения Python (кнопка в общих настройках).
     // $mcp_fix_result виден и внутри python.php (require ниже — в этом же scope).
     $mcp_fix_result = null;
     if ($this->view_mode == 'fix_python') {
         $mcp_fix_result = $this->fixPythonEnvironment();
         // Показываем результат вместе со свежей проверкой окружения.
         $this->view_mode = 'search_mcpDevice';
     }

     if (isset($this->data_source) && !$_GET['data_source'] && !$_POST['data_source']) {
         $out['SET_DATASOURCE']=1;
     }

     // Синхронизация устройств из штатного реестра MajorDoMo (таблица devices).
     if ($this->view_mode == 'sync_mcpDevice') {
         $sync = $this->syncMajorDoMoDevices();
         $out['SYNC_DONE'] = 1;
         $out['SYNC_TOTAL'] = $sync['total'];
         $out['SYNC_ADDED'] = $sync['added'];
         $out['SYNC_EXISTS'] = $sync['exists'];
         $out['SYNC_SKIPPED'] = $sync['skipped'];
         $this->view_mode = '';
     }

     if ($this->view_mode=='' || $this->view_mode=='search_mcpDevice') {
         $this->search_mcpDevice($out);
		 if ($this->python) require ('python.php');
     }
     if ($this->view_mode == 'add_mcpDevice') {
         $this->add_mcpDevice($out);
     }
	 if ($this->view_mode == 'add_mcpSmart') {
         $this->add_mcpSmart($out);
     }
    if ($this->view_mode=='edit_mcpDevice') {
        $this->edit_mcpDevice($out, (int)$this->id);
     }
     if ($this->view_mode=='delete_mcpDevice') {
        $this->delete_mcpDevice((int)$this->id);
         $this->redirect("?");
     }
     if ($this->view_mode=='search_mcpScene') {
         $this->search_mcpScene($out);
     }
     if ($this->view_mode=='add_mcpScene') {
         $this->add_mcpScene($out);
     }
     if ($this->view_mode=='edit_mcpScene') {
         $this->edit_mcpScene($out, (int)$this->id);
     }
     if ($this->view_mode=='delete_mcpScene') {
         $this->delete_mcpScene((int)$this->id);
         $this->redirect("?view_mode=search_mcpScene");
     }
     if ($this->view_mode=='search_mcpSchedule') {
         $this->search_mcpSchedule($out);
     }
     if ($this->view_mode=='add_mcpSchedule') {
         $this->add_mcpSchedule($out);
     }
     if ($this->view_mode=='edit_mcpSchedule') {
         $this->edit_mcpSchedule($out, (int)$this->id);
     }
     if ($this->view_mode=='delete_mcpSchedule') {
         $this->delete_mcpSchedule((int)$this->id);
         $this->redirect("?view_mode=search_mcpSchedule");
     }
     if ($this->view_mode=='toggle_mcpSchedule') {
         $this->toggle_mcpSchedule((int)$this->id);
     }
     if ($this->view_mode=='search_mcpServer') {
         $this->search_mcpServer($out);
     }
     if ($this->view_mode=='add_mcpServer') {
         $this->add_mcpServer($out);
     }
     if ($this->view_mode=='upload_mcpServer') {
         $this->upload_mcpServer($out);
     }
     if ($this->view_mode=='delete_mcpServer') {
         $this->delete_mcpServer($out);
     }
 }
 /**
 * FrontEnd
 *
 * Module frontend
 *
 * @access public
 */
 function usual(&$out) {
	 if ($this->ajax) {

         $op = gr('op');

         if ($op == 'generateClientId') {
             $client_id = sprintf('%04X%04X%04X-%04X%04X', mt_rand(0, 65535), mt_rand(0, 65535), mt_rand(0, 65535), mt_rand(16384, 20479), mt_rand(32768, 49151));
             $this->WriteLog("Generate new Client ID {$client_id}");
             exit (strtolower($client_id));
         } else if ($op == 'generateClientKey') {
             $client_key = sprintf('%04X%04X-%04X-%04X-%04X-%04X%04X%04X', mt_rand(0, 65535), mt_rand(0, 65535), mt_rand(0, 65535), mt_rand(16384, 20479), mt_rand(32768, 49151), mt_rand(0, 65535), mt_rand(0, 65535), mt_rand(0, 65535));
             $this->WriteLog("Generate new Client KEY {$client_key}");
             exit (strtolower($client_key));
         } else if ($op == 'sendSyncRequest') {
             // TODO
             exit ('OK');
         } else if ($op == 'startDetectSpeaker') {
             // Кнопка «Определить колонку»: «вооружаем» ожидание на 10 сек.
             // Целевое устройство (0 — форма добавления, устройство ещё не
             // сохранено) и данные формы пишутся в mcp_detect, чтобы
             // инструмент identify_speaker знал, куда записать ID.
             $this->ensureDetectTable();
             $rec = array(
                 'ID' => 1,
                 'TS' => time(),
                 'TARGET_ID' => (int)gr('target'),
                 'TITLE' => trim(gr('title')),
                 'ALIACE' => trim(gr('aliace')),
                 'LOCATION' => trim(gr('location')),
                 'RESULT' => '',
                 'RESULT_DEVICE_ID' => '',
                 'RESULT_ROOM' => '',
                 'RESULT_TITLE' => '',
                 'NEW_ID' => 0,
             );
             if (SQLSelectOne("SELECT ID FROM mcp_detect WHERE ID=1")) {
                 SQLUpdate('mcp_detect', $rec, 'ID');
             } else {
                 SQLInsert('mcp_detect', $rec);
             }
             exit ('OK');
         } else if ($op == 'getIdentifiedSpeaker') {
             // Опрос формы во время ожидания: identify_speaker уже записал
             // ID в поле устройства (state=ok) либо сообщил, что колонка уже
             // определена в комнате (state=already) — отдаём состояние формы.
             $result = array('state' => 'waiting', 'device_id' => '', 'room' => '', 'title' => '', 'new_id' => 0);
             $this->ensureDetectTable();
             $det = SQLSelectOne("SELECT * FROM mcp_detect WHERE ID=1");
             if (!$det || !$det['TS'] || (time() - (int)$det['TS']) > 15) {
                 $result['state'] = 'expired';
             } elseif ($det['RESULT'] == 'already') {
                 $result['state'] = 'already';
                 $result['room'] = $det['RESULT_ROOM'];
                 $result['title'] = $det['RESULT_TITLE'];
             } elseif ($det['RESULT'] == 'ok') {
                 $result['state'] = 'ok';
                 $result['new_id'] = (int)$det['NEW_ID'];
                 if ($result['new_id'] > 0) {
                     $result['device_id'] = $det['RESULT_DEVICE_ID'];
                 } else {
                     // Читаем значение прямо из поля устройства — именно оно
                     // должно появиться в форме.
                     $dev = SQLSelectOne("SELECT MAC FROM mcp_devices WHERE ID=" . (int)$det['TARGET_ID']);
                     $result['device_id'] = $dev ? $dev['MAC'] : $det['RESULT_DEVICE_ID'];
                 }
             }
             header('Content-Type: application/json; charset=utf-8');
             exit (json_encode($result));
         } else if ($op == 'checkSpeakerId') {
             // Проверка вручную введённого ID: не занят ли он другой колонкой.
             $found = $this->findSpeakerDevice(gr('device_id'), (int)gr('except_id'));
             header('Content-Type: application/json; charset=utf-8');
             exit (json_encode(array(
                 'exists' => $found ? 1 : 0,
                 'room' => $found ? $found['ROOM'] : '',
                 'title' => $found ? $found['TITLE'] : '',
             )));
         }

         echo 'OK';
     }
// $this->admin($out);
 }
 /**
 * Само-восстановление схемы mcp_devices.
 *
 * Добавляет недостающие колонки (например MAC), чтобы обновление модуля
 * не требовало переустановки таблицы.
 */
 function ensureDevicesSchema() {
     $result = SQLSelect("SHOW COLUMNS FROM mcp_devices");
     $existing = [];
     foreach ($result as $column) {
         $existing[strtoupper($column['Field'])] = true;
     }
     if (!isset($existing['MAC'])) {
         SQLExec("ALTER TABLE mcp_devices ADD COLUMN MAC varchar(64) NOT NULL DEFAULT ''");
     }
 }

 /**
 * mcp_devices search
 *
 * @access public
 */
 function search_mcpDevice(&$out) {
     $this->ensureDevicesSchema();
     require(dirname(__FILE__).'/device_search.inc.php');
 }

 function add_mcpDevice(&$out) {
     $this->ensureDevicesSchema();
     require(dirname(__FILE__).'/device_add.inc.php');
 }
 
 function add_mcpSmart(&$out) {
     require(dirname(__FILE__).'/smart_add.inc.php');
 }
 /**
 * mcp_devices edit/add
 *
 * @access public
 */
 function edit_mcpDevice(&$out, $id) {
     $this->ensureDevicesSchema();
     require(dirname(__FILE__).'/device_edit.inc.php');
 }
 
 /**
 * mcp_devices delete record
 *
 * @access public
 */
function delete_mcpDevice($id) {
    $id = (int)$id;
    if ($id <= 0) {
        return;
    }
    $this->DeleteLinkedProperties($id);
    SQLExec("DELETE FROM mcp_devices WHERE ID=" . $id);
 }

 /**
 * Синхронизация устройств из штатного реестра MajorDoMo.
 *
 * Источник — таблица devices (устройства, добавленные в MajorDoMo) и их
 * привязка devices.LINKED_OBJECT. Тип и умения выводятся из devices.TYPE по
 * тем же правилам, что использует модуль devices:
 *   relay/switch -> on(status); dimmer -> on(status)+brightness(level);
 *   rgb -> on(status)+brightness(brightness)+rgb(color);
 *   sensor_* -> sensor(value); motion -> motion_sensor(status);
 *   openable -> open(status/level/pause); thermostat/vacuum -> профильные умения.
 *
 * Устройства, уже добавленные в модуль (совпадает LINKED_OBJECT), не трогаются,
 * поэтому повторный запуск безопасен. Кнопки (button) пропускаются.
 *
 * @access public
 * @return array total/added/exists/skipped/linked
 */
 function syncMajorDoMoDevices() {
     $res = ['total' => 0, 'added' => 0, 'exists' => 0, 'skipped' => 0, 'linked' => 0];

     $this->ensureDevicesSchema();

     // Индекс уже добавленных устройств по связанному объекту (LINKED_OBJECT внутри TRAITS).
     $existing = SQLSelect("SELECT ID, TRAITS FROM mcp_devices");
     $by_object = [];
     foreach ($existing as $ex) {
         foreach ($this->iterTraits(json_decode($ex['TRAITS'], true)) as $trait) {
             $linked_object = isset($trait['linked_object']) ? trim($trait['linked_object']) : '';
             if ($linked_object !== '') {
                 $by_object[$linked_object] = (int)$ex['ID'];
             }
         }
     }

     $devices = SQLSelect(
         "SELECT d.ID, d.TITLE, d.TYPE, d.LINKED_OBJECT, d.ARCHIVED, "
         . "l.TITLE AS ROOM, o.ID AS OBJ_ID, o.CLASS_ID AS OBJ_CLASS_ID "
         . "FROM devices d "
         . "LEFT JOIN locations l ON l.ID = d.LOCATION_ID "
         . "LEFT JOIN objects o ON o.TITLE = d.LINKED_OBJECT "
         . "ORDER BY d.ID"
     );

     foreach ($devices as $dev) {
         $res['total']++;

         $type = strtolower(trim($dev['TYPE']));
         $linked_object = trim($dev['LINKED_OBJECT']);

         // Кнопки — это события ввода, ими не управляют голосом; пропускаем.
         if ($type === 'button') {
             $res['skipped']++;
             continue;
         }
         // Архивные устройства и записи без привязки к объекту не переносим.
         if ((int)$dev['ARCHIVED'] || $linked_object === '') {
             $res['skipped']++;
             continue;
         }

         $map = $this->mapMajorDoMoDevice($dev['TYPE'], $dev['TITLE']);
         if ($map === null) {
             $res['skipped']++;
             continue;
         }
         list($mcp_type, $trait_properties) = $map;

         // Устройство уже есть в модуле — не дублируем и не перезаписываем настройки.
         if (isset($by_object[$linked_object])) {
             $res['exists']++;
             continue;
         }

         // Какие свойства реально есть у связанного объекта (свойства объекта + цепочка классов).
         // Нужно, чтобы корректно подобрать свойство для нестандартных устройств.
         $available = $this->getObjectPropertyNames(
             isset($dev['OBJ_ID']) ? $dev['OBJ_ID'] : 0,
             isset($dev['OBJ_CLASS_ID']) ? $dev['OBJ_CLASS_ID'] : 0
         );

         $traits = [];
         foreach ($trait_properties as $trait_type => $candidates) {
             $description = isset($this->devices_instance[$trait_type]['description'])
                 ? $this->devices_instance[$trait_type]['description']
                 : '';
             $traits[$trait_type] = [
                 'type' => $trait_type,
                 'description' => $description,
                 'linked_object' => $linked_object,
                 'linked_property' => $this->pickLinkedProperty($candidates, $available),
                 'reportable' => false,
             ];
         }
         if (empty($traits)) {
             $res['skipped']++;
             continue;
         }

         $rec = [
             'TITLE' => $dev['TITLE'],
             'ALIACE' => $this->buildDeviceAliases($dev['TITLE'], $mcp_type),
             'TYPE' => $mcp_type,
             'ROOM' => $dev['ROOM'] ? $dev['ROOM'] : '',
             'DESCRIPTION' => '',
             'MANUFACTURER' => '',
             'MODEL' => '',
             'SW_VERSION' => '',
             'HW_VERSION' => '',
             'MAC' => '',
             'TRAITS' => json_encode($traits, JSON_UNESCAPED_UNICODE),
         ];
         $rec['ID'] = SQLInsert('mcp_devices', $rec);
         $rec['CONFIG'] = $this->buildDeviceConfig($rec);
         SQLUpdate('mcp_devices', $rec);

         $by_object[$linked_object] = (int)$rec['ID'];

         // Регистрируем модуль как потребителя связанных свойств (как при ручном редактировании).
         foreach ($traits as $trait) {
             addLinkedProperty($linked_object, $trait['linked_property'], $this->name);
             $res['linked']++;
         }
         $res['added']++;
     }

     $this->WriteLog("syncMajorDoMoDevices: добавлено {$res['added']}, уже было {$res['exists']}, пропущено {$res['skipped']} (всего {$res['total']})");
     return $res;
 }

 /**
 * Соответствие типа устройства MajorDoMo (devices.TYPE) типу и умениям модуля.
 *
 * @access public
 * @param string $type  значение devices.TYPE
 * @param string $title название устройства (уточняет неоднозначные типы)
 * @return array|null [тип модуля, [умение => свойство объекта]] либо null
 */
 function mapMajorDoMoDevice($type, $title = '') {
     $type = strtolower(trim($type));
     $is = function ($pattern) use ($title) {
         return (bool)preg_match($pattern, (string)$title);
     };

     switch ($type) {
         case 'relay':
             // В MajorDoMo и свет, и розетка хранятся как relay — уточняем по названию.
             if ($is('/свет|люстр|ламп|подсвет|бра|торшер|освещ/ui')) {
                 return ['light', ['on' => ['status']]];
             }
             if ($is('/розетк|socket/ui')) {
                 return ['socket', ['on' => ['status']]];
             }
             return ['switch', ['on' => ['status']]];
         case 'switch':
             return ['switch', ['on' => ['status']]];
         case 'dimmer':
             return ['light', ['on' => ['status'], 'brightness' => ['level', 'brightness']]];
         case 'rgb':
             return ['light', ['on' => ['status'], 'brightness' => ['brightness', 'level'], 'rgb' => ['color']]];
         case 'motion':
             return ['sensor', ['motion_sensor' => ['status', 'motion']]];
         case 'openable':
         case 'openclose':
             if ($is('/штор|жалюз|карниз|cornice|ornice|curtain/ui')) {
                 return ['openable.curtain', ['on' => ['status'], 'open' => ['level', 'levelWork'], 'pause' => ['pause']]];
             }
             return ['openable', ['on' => ['status'], 'open' => ['level', 'levelWork'], 'pause' => ['pause']]];
         case 'sensor_temp':
             return ['sensor', ['temperature_sensor' => ['value', 'TempCurrent']]];
         case 'sensor_temphum':
             return ['sensor', ['temperature_sensor' => ['value', 'TempCurrent'], 'humidity_sensor' => ['valueHumidity', 'humidity']]];
         case 'sensor_hum':
         case 'sensor_humidity':
             return ['sensor', ['humidity_sensor' => ['value', 'valueHumidity', 'humidity']]];
         case 'sensor_light':
             return ['sensor', ['illumination_sensor' => ['value']]];
         case 'sensor_pressure':
             return ['sensor', ['pressure_sensor' => ['value']]];
         case 'sensor_co2':
             return ['sensor', ['co2_level_sensor' => ['value']]];
         case 'sensor_voltage':
             return ['sensor', ['voltage_sensor' => ['value']]];
         case 'sensor_amperage':
             return ['sensor', ['amperage_sensor' => ['value']]];
         case 'sensor_power':
             return ['sensor', ['power_sensor' => ['value']]];
         case 'sensor_battery':
             return ['sensor', ['battery_level_sensor' => ['value', 'batteryLevel']]];
         case 'sensor_water':
             return ['sensor', ['water_level_sensor' => ['value']]];
         case 'sensor_leak':
         case 'leak':
             return ['sensor', ['water_leak_sensor' => ['status']]];
         case 'sensor_smoke':
         case 'smoke':
             return ['sensor', ['smoke_sensor' => ['status']]];
         case 'sensor_gas':
         case 'gas':
             return ['sensor', ['gas_sensor' => ['status']]];
         case 'thermostat':
             return ['thermostat', ['on' => ['relay_status', 'Mode', 'status'], 'temperature' => ['TempSet', 'normalTargetValue', 'currentTargetValue']]];
         case 'vacuum':
         case 'vacuum_cleaner':
         case 'roborock_vacuum':
             return ['vacuum_cleaner', ['on' => ['status', 'command', 'state'], 'fan_speed' => ['mode', 'fan_speed']]];
     }
     return null;
 }

 /**
 * Псевдонимы устройства для голосового поиска (поле ALIACE).
 *
 * @access public
 * @param string $title      название устройства
 * @param string $type       тип устройства в модуле
 * @return string псевдонимы через запятую
 */
 function buildDeviceAliases($title, $type = '') {
     $aliases = [];
     $add = function ($value) use (&$aliases) {
         $value = trim((string)$value);
         if ($value === '') {
             return;
         }
         foreach (preg_split('/[,;]+/u', $value) as $part) {
             $part = trim($part);
             if ($part !== '' && !in_array($part, $aliases, true)) {
                 $aliases[] = $part;
             }
         }
     };

     $add($title);

     // Дополнительный псевдоним без общих типовых слов: «Свет над столом» -> «над столом».
     $generic = ['свет', 'светильник', 'лампа', 'люстра', 'розетка', 'датчик', 'сенсор', 'освещение', 'подсветка', 'бра', 'торшер'];
     $rest = [];
     foreach (preg_split('/\s+/u', trim((string)$title)) as $word) {
         $clean = preg_replace('/[^\p{L}\p{N}]+/u', '', $word);
         if ($clean === '') {
             continue;
         }
         $lower = function_exists('mb_strtolower') ? mb_strtolower($clean, 'UTF-8') : strtolower($clean);
         if (in_array($lower, $generic, true)) {
             continue;
         }
         $rest[] = $clean;
     }
     if (!empty($rest)) {
         $add(implode(' ', $rest));
     }

     return implode(', ', $aliases);
 }

 /**
 * Единый обход TRAITS (поддержаны dict и list) — как в Python-части.
 *
 * @access public
 */
 function iterTraits($traits) {
     if (!is_array($traits)) {
         return [];
     }
     $result = [];
     foreach ($traits as $trait) {
         if (is_array($trait) && isset($trait['type']) && $trait['type'] !== '') {
             $result[] = $trait;
         }
     }
     return $result;
 }

 /**
  * Список доступных свойств объекта MajorDoMo: собственные свойства объекта
  * плюс свойства по цепочке классов (регистр не важен).
  *
  * @access public
  * @param int $obj_id    ID объекта (objects.ID)
  * @param int $class_id  ID класса объекта (objects.CLASS_ID)
  * @return array ключ (нижний регистр) => имя свойства
  */
 function getObjectPropertyNames($obj_id, $class_id) {
     $result = [];
     $obj_id = (int)$obj_id;
     if ($obj_id > 0) {
         $rows = SQLSelect("SELECT TITLE FROM properties WHERE OBJECT_ID=" . $obj_id);
         foreach ($rows as $r) {
             $key = function_exists('mb_strtolower') ? mb_strtolower($r['TITLE'], 'UTF-8') : strtolower($r['TITLE']);
             $result[$key] = $r['TITLE'];
         }
     }
     $class_id = (int)$class_id;
     $guard = 0;
     while ($class_id > 0 && $guard < 25) {
         $cls = SQLSelectOne("SELECT ID, PARENT_ID FROM classes WHERE ID=" . $class_id);
         if (!is_array($cls) || !isset($cls['ID'])) {
             break;
         }
         $rows = SQLSelect("SELECT TITLE FROM properties WHERE CLASS_ID=" . (int)$cls['ID']);
         foreach ($rows as $r) {
             $key = function_exists('mb_strtolower') ? mb_strtolower($r['TITLE'], 'UTF-8') : strtolower($r['TITLE']);
             if (!isset($result[$key])) {
                 $result[$key] = $r['TITLE'];
             }
         }
         $class_id = (int)$cls['PARENT_ID'];
         $guard++;
     }
     return $result;
 }

 /**
  * Выбор свойства для умения: первое подходящее из candidates, которое реально
  * есть у объекта; иначе — первый кандидат (поведение по умолчанию).
  *
  * @access public
  * @param string|array $candidates свойство или список свойств
  * @param array $available доступные свойства (из getObjectPropertyNames)
  * @return string
  */
 function pickLinkedProperty($candidates, $available) {
     if (is_string($candidates)) {
         $candidates = [$candidates];
     }
     if (!is_array($candidates) || empty($candidates)) {
         return '';
     }
     if (is_array($available) && !empty($available)) {
         foreach ($candidates as $candidate) {
             $key = function_exists('mb_strtolower') ? mb_strtolower($candidate, 'UTF-8') : strtolower($candidate);
             if (isset($available[$key])) {
                 return $available[$key];
             }
         }
     }
     return $candidates[0];
 }

 /**
 * Сборка CONFIG устройства (формат API Yandex Home) из TRAITS.
 *
 * Используется и при ручном редактировании (device_edit.inc.php), и при
 * синхронизации устройств из MajorDoMo.
 *
 * @access public
 * @param array $rec                  запись устройства (ID, TITLE, ALIACE, TYPE, ROOM, ...)
 * @param array|null $devices_instance конфигурация умений; по умолчанию — штатная
 * @return string JSON-конфиг
 */
 function buildDeviceConfig($rec, $devices_instance = null) {
     if (!is_array($devices_instance)) {
         $devices_instance = $this->devices_instance;
     }

     $new_dev_traits = json_decode(isset($rec['TRAITS']) ? $rec['TRAITS'] : '', true);

     $traits = [];
     $properties = [];
     if (is_array($new_dev_traits)) {
         foreach ($new_dev_traits as $trait) {
             if (!is_array($trait) || !isset($trait['type']) || !isset($this->devices_instance[$trait['type']])) {
                 continue;
             }
             $parameters = [];
             $trait['reportable'] = isset($trait['reportable']) ? (bool)$trait['reportable'] : false;

             $capability = $this->devices_instance[$trait['type']]['capability'];
             $is_sensor = ($capability == 'float' || $capability == 'event');

             $trait_type = ($is_sensor ? PREFIX_PROPERTIES : PREFIX_CAPABILITIES) . $capability;
             $instance_name = $is_sensor ? str_replace('_sensor', '', $trait['type']) : $trait['type'];

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
             if ($check !== false && $trait_type == PREFIX_CAPABILITIES . 'color_setting') {
                 $traits[$check]['parameters'] = array_merge($traits[$check]['parameters'], $parameters);
             } else {
                 if (isset($this->devices_instance[$trait['type']]['retrievable'])) {
                     $retrievable = $this->devices_instance[$trait['type']]['retrievable'];
                 } else {
                     $retrievable = true;
                 }
                 $entry = [
                     'type' => $trait_type,
                     'parameters' => $parameters,
                     'retrievable' => $retrievable,
                     'reportable' => $trait['reportable']
                 ];
                 if ($is_sensor) {
                     $properties[] = $entry;
                 } else {
                     $traits[] = $entry;
                 }
             }
         }
     }

     return json_encode([
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
 }

 /**
 * DDL-суффикс с движком таблицы.
 *
 * Предпочитаем InnoDB, но на сборках СУБД с отключённым InnoDB
 * (SHOW ENGINES → Support=NO) CREATE TABLE ... ENGINE=InnoDB
 * падает с ошибкой 1286 «Unknown storage engine 'InnoDB'». Тогда движок
 * не указываем — сервер возьмёт свой по умолчанию (обычно MyISAM).
 */
 function tableEngineSuffix() {
     static $suffix = null;
     if ($suffix !== null) {
         return $suffix;
     }
     $suffix = ' DEFAULT CHARSET=utf8mb4';
     $row = SQLSelectOne("SELECT SUPPORT FROM information_schema.ENGINES WHERE ENGINE='InnoDB'");
     if ($row) {
         $support = strtoupper(trim((string)reset($row)));
         if ($support !== '' && $support !== 'NO') {
             $suffix = ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4';
         }
     }
     return $suffix;
 }

 /**
 * mcp_scenes search / add / edit / delete
 *
 * Сцены (группы) — набор заранее настроенных действий: устройство + вкл/выкл (+ яркость).
 * Используются голосом: «включи вечерний свет».
 */
 /**
  * Таблица режима ожидания «Определить колонку» (кнопка в форме устройства).
  * Одна строка ID=1: «вооружение» ставит op=startDetectSpeaker, результат
  * пишет инструмент identify_speaker (Python), опрашивает getIdentifiedSpeaker.
  */
 function ensureDetectTable() {
     SQLExec(
         "CREATE TABLE IF NOT EXISTS mcp_detect ("
         . " ID int unsigned NOT NULL PRIMARY KEY,"
         . " TS bigint NOT NULL DEFAULT 0,"
         . " TARGET_ID int NOT NULL DEFAULT 0,"
         . " TITLE varchar(255) NOT NULL DEFAULT '',"
         . " ALIACE varchar(255) NOT NULL DEFAULT '',"
         . " LOCATION varchar(255) NOT NULL DEFAULT '',"
         . " RESULT varchar(32) NOT NULL DEFAULT '',"
         . " RESULT_DEVICE_ID varchar(64) NOT NULL DEFAULT '',"
         . " RESULT_ROOM varchar(255) NOT NULL DEFAULT '',"
         . " RESULT_TITLE varchar(255) NOT NULL DEFAULT '',"
         . " NEW_ID int NOT NULL DEFAULT 0"
         . ")" . $this->tableEngineSuffix()
     );
 }

 /**
  * Ищет устройство-колонку, у которой уже вписан этот ID (поле MAC = ID
  * устройства XiaoZhi либо MAC колонки). Нормализация та же, что в
  * Python-резолвере: сравниваются только hex-символы без учёта регистра.
  *
  * $except_id — устройство, которому этот ID принадлежит по праву
  * (его не считаем занятым). Возвращает запись (assoc) либо false.
  */
 function findSpeakerDevice($device_id, $except_id = 0) {
     $norm = strtoupper(preg_replace('/[^0-9A-Fa-f]/', '', (string)$device_id));
     if ($norm === '') {
         return false;
     }
     $rows = SQLSelect("SELECT ID, TITLE, ROOM, MAC FROM mcp_devices WHERE MAC <> ''");
     if ($rows) {
         foreach ($rows as $row) {
             if ((int)$row['ID'] == (int)$except_id) {
                 continue;
             }
             if (strtoupper(preg_replace('/[^0-9A-Fa-f]/', '', (string)$row['MAC'])) == $norm) {
                 return $row;
             }
         }
     }
     return false;
 }

 function ensureScenesTable() {
     SQLExec(
         "CREATE TABLE IF NOT EXISTS mcp_scenes ("
         . " ID int(10) unsigned NOT NULL auto_increment,"
         . " TITLE varchar(255) NOT NULL DEFAULT '',"
         . " ALIACE varchar(255) NOT NULL DEFAULT '',"
         . " ENABLED tinyint(1) NOT NULL DEFAULT 1,"
         . " ACTIONS text,"
         . " CREATED datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,"
         . " UPDATED timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
         . " PRIMARY KEY (ID)"
         . ")" . $this->tableEngineSuffix()
     );
 }

 function search_mcpScene(&$out) {
     $this->ensureScenesTable();
     require(dirname(__FILE__).'/scene_search.inc.php');
 }

 function add_mcpScene(&$out) {
     $this->ensureScenesTable();
     require(dirname(__FILE__).'/scene_edit.inc.php');
 }

 function edit_mcpScene(&$out, $id) {
     $this->ensureScenesTable();
     require(dirname(__FILE__).'/scene_edit.inc.php');
 }

 function delete_mcpScene($id) {
     $id = (int)$id;
     if ($id <= 0) {
         return;
     }
     SQLExec("DELETE FROM mcp_scenes WHERE ID=" . $id);
 }

 /**
 * Планировщик задач: self-healing таблица + CRUD из админки.
 *
 * Данные хранит таблица mcp_schedule; задания выполняет schedule(), которая
 * вызывается из processCycle() (раз в цикл MajorDoMo).
 */
 function ensureScheduleTable() {
     SQLExec(
         "CREATE TABLE IF NOT EXISTS mcp_schedule ("
         . " ID int(10) unsigned NOT NULL auto_increment,"
         . " TASK_ID varchar(255) NOT NULL DEFAULT '',"
         . " TITLE varchar(255) NOT NULL DEFAULT '',"
         . " ENABLED tinyint(1) NOT NULL DEFAULT 0,"
         . " SCHEDULE_TIME varchar(10) NOT NULL DEFAULT '',"
         . " SCHEDULE_DAYS varchar(255) NOT NULL DEFAULT '',"
         . " SCHEDULE_DATE date DEFAULT NULL,"
         . " ACTION_TYPE varchar(50) NOT NULL DEFAULT '',"
         . " ACTION_TARGET varchar(255) NOT NULL DEFAULT '',"
         . " ACTION_STATE varchar(100) NOT NULL DEFAULT '',"
         . " ACTION_DATA text,"
         . " LAST_RUN datetime DEFAULT NULL,"
         . " CREATED datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,"
         . " UPDATED timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
         . " PRIMARY KEY (ID)"
         . ")" . $this->tableEngineSuffix()
     );
     // Догоняем уже установленные модули: добавляем недостающие колонки.
     $columns = SQLSelect("SHOW COLUMNS FROM mcp_schedule");
     $existing = [];
     foreach ($columns as $column) {
         $existing[strtoupper($column['Field'])] = true;
     }
     if (!isset($existing['LAST_RUN'])) {
         SQLExec("ALTER TABLE mcp_schedule ADD COLUMN LAST_RUN datetime DEFAULT NULL");
     }
     if (!isset($existing['SCHEDULE_DATE'])) {
         SQLExec("ALTER TABLE mcp_schedule ADD COLUMN SCHEDULE_DATE date DEFAULT NULL");
     }
 }

 function search_mcpSchedule(&$out) {
     $this->ensureScheduleTable();
     require(dirname(__FILE__).'/schedule_search.inc.php');
 }

 function add_mcpSchedule(&$out) {
     $this->ensureScheduleTable();
     require(dirname(__FILE__).'/schedule_edit.inc.php');
 }

 function edit_mcpSchedule(&$out, $id) {
     $this->ensureScheduleTable();
     require(dirname(__FILE__).'/schedule_edit.inc.php');
 }

 function delete_mcpSchedule($id) {
     $id = (int)$id;
     if ($id <= 0) {
         return;
     }
     SQLExec("DELETE FROM mcp_schedule WHERE ID=" . $id);
 }

 function toggle_mcpSchedule($id) {
     $id = (int)$id;
     if ($id <= 0) {
         return;
     }
     $rec = SQLSelectOne("SELECT ID, ENABLED FROM mcp_schedule WHERE ID=" . $id);
     if (!$rec) {
         return;
     }
     $rec['ENABLED'] = $rec['ENABLED'] ? 0 : 1;
     SQLUpdate('mcp_schedule', $rec);
     $this->redirect('?view_mode=search_mcpSchedule');
 }

 /**
  * Нормализует SCHEDULE_DAYS к списку канонических токенов.
  *
  * Результат: ["once"] либо подмножество ["mon".."sun"] в каноническом порядке.
  * Понимает JSON-массив, строку через запятую, а также daily/weekdays/weekends
  * и русские названия дней.
  */
 function normalizeScheduleDays($raw) {
     $order = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
     $aliases = [
         'mon' => 'mon', 'monday' => 'mon', 'пн' => 'mon', 'пон' => 'mon', 'понедельник' => 'mon',
         'tue' => 'tue', 'tues' => 'tue', 'tuesday' => 'tue', 'вт' => 'tue', 'вторник' => 'tue',
         'wed' => 'wed', 'weds' => 'wed', 'wednesday' => 'wed', 'ср' => 'wed', 'среда' => 'wed',
         'thu' => 'thu', 'thur' => 'thu', 'thurs' => 'thu', 'thursday' => 'thu', 'чт' => 'thu', 'четверг' => 'thu',
         'fri' => 'fri', 'friday' => 'fri', 'пт' => 'fri', 'пятница' => 'fri',
         'sat' => 'sat', 'saturday' => 'sat', 'сб' => 'sat', 'суббота' => 'sat',
         'sun' => 'sun', 'sunday' => 'sun', 'вс' => 'sun', 'воскресенье' => 'sun',
         'once' => 'once', 'onetime' => 'once', 'разово' => 'once', 'одноразово' => 'once',
         'daily' => 'daily', 'everyday' => 'daily', 'каждый' => 'daily', 'ежедневно' => 'daily',
         'weekdays' => 'weekdays', 'weekday' => 'weekdays', 'будни' => 'weekdays', 'рабочие' => 'weekdays',
         'weekends' => 'weekends', 'weekend' => 'weekends', 'выходные' => 'weekends',
         'all' => 'daily', 'все' => 'daily',
     ];

     if (is_array($raw)) {
         $items = $raw;
     } else {
         $decoded = json_decode((string)$raw, true);
         if (is_array($decoded)) {
             $items = $decoded;
         } else {
             $items = preg_split('/[,;\s]+/', (string)$raw);
         }
     }

     $result = [];
     $seen = [];
     foreach ($items as $item) {
         $key = mb_strtolower(trim((string)$item), 'UTF-8');
         $key = str_replace('ё', 'е', $key);
         if ($key === '') {
             continue;
         }
         $mapped = isset($aliases[$key]) ? $aliases[$key] : null;
         if ($mapped === null && mb_strlen($key) >= 3) {
             foreach ($aliases as $alias => $canon) {
                 if (mb_strlen($alias) >= 3 && strpos($key, $alias) === 0) {
                     $mapped = $canon;
                     break;
                 }
             }
         }
         if ($mapped === null) {
             continue;
         }
         if ($mapped === 'once') {
             return ['once'];
         }
         if ($mapped === 'daily') {
             return $order;
         }
         if ($mapped === 'weekdays') {
             foreach (array_slice($order, 0, 5) as $day) {
                 $result[$day] = true;
             }
         } elseif ($mapped === 'weekends') {
             foreach (array_slice($order, 5) as $day) {
                 $result[$day] = true;
             }
         } else {
             $result[$mapped] = true;
         }
     }

     if (empty($result)) {
         return $order;
     }
     $normalized = [];
     foreach ($order as $day) {
         if (isset($result[$day])) {
             $normalized[] = $day;
         }
     }
     return $normalized;
 }

 /**
  * Находит запись устройства для задания планировщика (по TITLE или ALIACE).
  */
 function findScheduleDeviceRecord($target) {
     $target = trim((string)$target);
     if ($target === '') {
         return null;
     }
     $device = SQLSelectOne("SELECT * FROM mcp_devices WHERE TITLE='" . DBSafe($target) . "'");
     if ($device) {
         return $device;
     }
     $candidates = SQLSelect("SELECT * FROM mcp_devices WHERE ALIACE LIKE '%" . DBSafe($target) . "%'");
     if (count($candidates) == 1) {
         return $candidates[0];
     }
     // Среди нескольких кандидатов ищем точное совпадение элемента списка алиасов.
     foreach ($candidates as $candidate) {
         foreach (preg_split('/[,;]/', (string)$candidate['ALIACE']) as $alias) {
             if (trim($alias) === $target) {
                 return $candidate;
             }
         }
     }
     return null;
 }

 /**
  * Возвращает канал управления [object, property] для устройства.
  *
  * Приоритет трейтов — по списку $wantedTypes, затем любой привязанный.
  */
 function scheduleTraitChannel($device, $wantedTypes = ['on']) {
     if (!$device) {
         return [null, null];
     }
     $traits = json_decode(isset($device['TRAITS']) ? $device['TRAITS'] : '', true);
     if (is_array($traits)) {
         foreach ($wantedTypes as $wanted) {
             foreach ($traits as $trait) {
                 if (is_array($trait)
                     && strtolower(isset($trait['type']) ? $trait['type'] : '') == $wanted
                     && !empty($trait['linked_object']) && !empty($trait['linked_property'])) {
                     return [$trait['linked_object'], $trait['linked_property']];
                 }
             }
         }
         foreach ($traits as $trait) {
             if (is_array($trait) && !empty($trait['linked_object']) && !empty($trait['linked_property'])) {
                 return [$trait['linked_object'], $trait['linked_property']];
             }
         }
     }
     if (!empty($device['LINKED_OBJECT']) && !empty($device['LINKED_PROPERTY'])) {
         return [$device['LINKED_OBJECT'], $device['LINKED_PROPERTY']];
     }
     return [null, null];
 }

 /**
  * Канал вкл/выкл для задания: сначала ACTION_DATA (его разрешил Python),
  * затем трейт «on» найденного устройства.
  */
 function resolveScheduleDevice($task) {
     $data = json_decode(isset($task['ACTION_DATA']) ? $task['ACTION_DATA'] : '', true);
     if (is_array($data) && !empty($data['linked_object']) && !empty($data['linked_property'])) {
         return [$data['linked_object'], $data['linked_property']];
     }
     $device = $this->findScheduleDeviceRecord(isset($task['ACTION_TARGET']) ? $task['ACTION_TARGET'] : '');
     return $this->scheduleTraitChannel($device, ['on']);
 }
 
 function propertySetHandle($object, $property, $value) {
      // Связка «устройство → метрика» хранится в mcp_devices.TRAITS (JSON).
      // Отдельных колонок LINKED_OBJECT/LINKED_PROPERTY в таблице нет, поэтому
      // прежний запрос к ним падал с «Unknown column». А MajorDoMo на любую
      // такую ошибку (lib/mysql.class.php) удаляет
      // cms/modules_installed/control_modules.installed — и на следующем
      // запросе заново устанавливает ВСЕ модули (перезапуск сервисов,
      // пересборка venv). Отсюда «веб-сервер подвисает». Сверяемся с TRAITS.
      $object = trim((string)$object);
      $property = trim((string)$property);
      if ($object === '' || $property === '') {
          return;
      }
      $devices = SQLSelect("SELECT ID, TRAITS FROM mcp_devices WHERE TRAITS LIKE '%linked_object%'");
      $total = count($devices);
      for ($i = 0; $i < $total; $i++) {
          $traits = json_decode(isset($devices[$i]['TRAITS']) ? $devices[$i]['TRAITS'] : '', true);
          if (!is_array($traits)) {
              continue;
          }
          foreach ($traits as $trait) {
              if (is_array($trait)
                  && isset($trait['linked_object'], $trait['linked_property'])
                  && $trait['linked_object'] === $object
                  && $trait['linked_property'] === $property) {
                  // to-do
              }
          }
      }
 }
 
 function schedule() {
     $this->getConfig();
     $this->ensureScheduleTable();

     $currentTime = date("H:i");
     $today = date("Y-m-d");
     $todayDow = (int)date("N"); // 1=Пн .. 7=Вс
     $dayToNum = ['mon' => 1, 'tue' => 2, 'wed' => 3, 'thu' => 4, 'fri' => 5, 'sat' => 6, 'sun' => 7];

     // Берём задания, время которых наступило: обычные — точно в эту минуту,
     // одноразовые с датой — как только наступили (догоняем пропущенное).
     $tasks = [];
     $dueNow = SQLSelect("SELECT * FROM mcp_schedule WHERE ENABLED = 1 AND SCHEDULE_TIME = '" . DBSafe($currentTime) . "'");
     foreach ($dueNow as $task) {
         $tasks[$task['ID']] = $task;
     }
     $missed = SQLSelect(
         "SELECT * FROM mcp_schedule WHERE ENABLED = 1"
         . " AND SCHEDULE_DATE IS NOT NULL AND SCHEDULE_DATE <= '" . DBSafe($today) . "'"
         . " AND SCHEDULE_TIME <= '" . DBSafe($currentTime) . "' AND LAST_RUN IS NULL"
     );
     foreach ($missed as $task) {
         $tasks[$task['ID']] = $task;
     }

     foreach ($tasks as $task) {
         $days = $this->normalizeScheduleDays($task['SCHEDULE_DAYS']);
         $isOnce = (count($days) === 1 && $days[0] === 'once');
         $taskDate = isset($task['SCHEDULE_DATE']) ? (string)$task['SCHEDULE_DATE'] : '';
         if ($taskDate === '0000-00-00') {
             $taskDate = '';
         }

         if ($isOnce) {
             if ($taskDate !== '') {
                 if ($taskDate < $today) {
                     // Просроченное одноразовое задание (например, «через 5 минут») не выполняем.
                     DebMes("Планировщик: просроченное задание #" . $task['ID'] . " удалено", $this->name);
                     SQLExec("DELETE FROM mcp_schedule WHERE ID = " . (int)$task['ID']);
                     continue;
                 }
                 if ($taskDate > $today) {
                     continue; // ещё не наступило
                 }
             }
             if (!empty($task['LAST_RUN'])) {
                 continue; // уже выполнялось
             }
         } else {
             $todayToken = array_search($todayDow, $dayToNum, true);
             if ($todayToken === false || !in_array($todayToken, $days, true)) {
                 continue; // не тот день недели
             }
             // Защита от двойного срабатывания в одну минуту.
             if (!empty($task['LAST_RUN']) && substr($task['LAST_RUN'], 0, 16) === date("Y-m-d H:i")) {
                 continue;
             }
         }

         $this->runScheduleTask($task);

         SQLExec("UPDATE mcp_schedule SET LAST_RUN=NOW() WHERE ID=" . (int)$task['ID']);
         if ($isOnce) {
             SQLExec("DELETE FROM mcp_schedule WHERE ID = " . (int)$task['ID']);
         }
     }
 }

 /**
  * Выполняет одно задание планировщика (вкл/выкл + яркость).
  */
 function runScheduleTask($task) {
     if ($task['ACTION_TYPE'] != 'device') {
         return;
     }

     $state = strtolower(trim((string)$task['ACTION_STATE']));
     $onWords = ['включи', 'включить', 'вкл', 'on', '1', 'да', 'true'];
     $offWords = ['выключи', 'выключить', 'выкл', 'off', '0', 'нет', 'false'];
     if (!in_array($state, $onWords, true) && !in_array($state, $offWords, true)) {
         DebMes("Планировщик: задание #" . $task['ID'] . " пропущено — неизвестное действие «" . $task['ACTION_STATE'] . "»", $this->name);
         return;
     }
     $isOn = in_array($state, $onWords, true);

     list($linkedObject, $linkedProperty) = $this->resolveScheduleDevice($task);
     if ($linkedObject === null || $linkedProperty === null) {
         DebMes("Планировщик: задание #" . $task['ID'] . " пропущено — устройство «" . $task['ACTION_TARGET'] . "» не найдено", $this->name);
         return;
     }

     setGlobal($linkedObject . '.' . $linkedProperty, $isOn ? "1" : "0");
     $this->WriteLog("Планировщик: [" . $task['ID'] . "] " . $task['ACTION_TARGET'] . " -> " . $linkedObject . '.' . $linkedProperty . "=" . ($isOn ? "1" : "0"));

     // Яркость (если задана в ACTION_DATA и устройство её поддерживает).
     $data = json_decode(isset($task['ACTION_DATA']) ? $task['ACTION_DATA'] : '', true);
     if ($isOn && is_array($data) && isset($data['brightness']) && $data['brightness'] !== '') {
         $device = $this->findScheduleDeviceRecord($task['ACTION_TARGET']);
         list($brightnessObject, $brightnessProperty) = $this->scheduleTraitChannel($device, ['brightness']);
         if ($brightnessObject !== null && $brightnessProperty !== null) {
             $level = max(0, min(100, (int)$data['brightness']));
             setGlobal($brightnessObject . '.' . $brightnessProperty, (string)$level);
             $this->WriteLog("Планировщик: яркость " . $brightnessObject . '.' . $brightnessProperty . "=" . $level);
         }
     }
 }
 
 /*
 *
 * Удаление всех линков на привязанные к метрикам устройства свойства.
 *
 */
 function DeleteLinkedProperties($id, $properties = false) {
     if (!$properties) {
        $id = (int)$id;
        if ($id <= 0) {
            return;
        }
        $properties = SQLSelectOne("SELECT TRAITS FROM mcp_devices WHERE ID=" . $id);
         $properties = json_decode($properties['TRAITS'], true);
     }

     if (is_array($properties) && !empty($properties)) {
         foreach ($properties as $prop) {
             $linked_object = $prop['linked_object'];
             $linked_property = $prop['linked_property'];
             if ($linked_object != '' && $linked_property != '') {
                  removeLinkedProperty($linked_object, $linked_property, $this->name);
                  $this->WriteLog("removeLinkedProperty for $linked_object and $linked_property");
             }
         }
     }
 }

// Функция для получения PID процесса по маркеру командной строки
 function getPidByName($processName, $num = 0) {
   if ($this->isWindowsPlatform()) {
       return $this->getWindowsPidsByName($processName, $num);
   }
    // Получаем PID текущего PHP-скрипта (для отладки и в качестве меры предосторожности)
    $currentPhpPid = getmypid();
    // echo $currentPhpPid . "\n"; // Можно раскомментировать для отладки

   // Ищем процессы, содержащие $processName в командной строке
    $cmd = "pgrep -f " . escapeshellarg($processName) . " 2>/dev/null";
    $raw_pids = [];
    $return_var = -1;
    exec($cmd, $raw_pids, $return_var);

    $filtered_pids = [];

    if ($return_var === 0 && !empty($raw_pids)) {
        foreach ($raw_pids as $pid_str) {
            $pid = (int)trim($pid_str);

           // Пропускаем PID 0 и сам PHP-скрипт
            if ($pid <= 0 || $pid == $currentPhpPid) {
                // Для отладки, если вдруг PID PHP скрипта был найден (редко, но возможно при определённых условиях)
                // echo "DEBUG: Skipping PID $pid (invalid or current PHP script)\n";
                continue;
            }

            // Проверяем командную строку процесса
            $cmdline_file = "/proc/$pid/cmdline";
            if (file_exists($cmdline_file)) {
                $cmdline_raw = file_get_contents($cmdline_file);
                // Аргументы в cmdline разделены null-байтами
                $cmdline_args = explode("\0", $cmdline_raw);
                // Объединяем в строку для проверки (аргументы, разделённые пробелом)
                $full_cmdline_string = implode(' ', $cmdline_args);

                // Целевой процесс: python, в аргументах которого ровно $processName.
                // Такая проверка устойчива к ложным срабатываниям на самой строке поиска и обёртках.
                $contains_target = in_array($processName, $cmdline_args)
                    && (stripos($cmdline_args[0] ?? '', 'python') !== false);
                // Исключаем сам поиск (pgrep) и его оболочки (sh -c / bash / ssh)
                $contains_pgrep = strpos($full_cmdline_string, 'pgrep') !== false;
                $contains_sh_c = strpos($full_cmdline_string, 'sh -c') !== false;
                $contains_bash = strpos($full_cmdline_string, 'bash') !== false;
                $contains_ssh = strpos($full_cmdline_string, 'ssh') !== false;

                // echo "DEBUG: PID $pid, Command: '$full_cmdline_string'\n"; // Для отладки
                // echo "       Contains Target ('$processName'): " . ($contains_target ? 'YES' : 'NO') . "\n"; // Для отладки;
                // echo "       Contains 'pgrep': " . ($contains_pgrep ? 'YES' : 'NO') . "\n"; // Для отладки
                // echo "       Contains 'sh -c': " . ($contains_sh_c ? 'YES' : 'NO') . "\n"; // Для отладки

                // Основная проверка для исключения ложных срабатываний
                // Процесс принимается, если:
                // 1. Он содержит $processName в командной строке (наш целевой критерий)
               // 2. Он НЕ содержит 'pgrep' (исключает сам процесс поиска)
               // 3. Он НЕ содержит 'sh -c' (исключает shell-обертку поиска)
                if ($contains_target && !$contains_pgrep && !$contains_sh_c && !$contains_bash && !$contains_ssh) {
                    // echo "       -> ACCEPTED\n"; // Для отладки
                    $filtered_pids[] = $pid;
                } else {
                    // echo "       -> REJECTED\n"; // Для отладки
                }
            } else {
               // Не удалось прочитать /proc/$pid/cmdline (процесс мог завершиться между шагами)
                // Failed to read cmdline for PID $pid (might have died)\n"; // Для отладки
                continue;
            }
        }
    }

    // echo "FINAL FILTERED: "; // Для отладки
    // print_r($filtered_pids); // Для отладки

    // Возвращаем отфильтрованный массив
    if (!empty($filtered_pids)) {
        // Если $num == 0, возвращаем первый найденный (если нужно только один)
        if ($num == 0) {
            return (int)reset($filtered_pids);
        }
        // Иначе возвращаем весь массив
        return $filtered_pids;
    }
    // Если ничего не найдено после фильтрации
    return null;
 }

 // Функция для проверки, запущен ли процесс с заданным PID
 function isProcessRunning($pid) {
     if ($pid === null || !is_numeric($pid)) {
         return false;
     }
    if ($this->isWindowsPlatform()) {
        $pid = (int)$pid;
        $output = [];
        $return_var = -1;
        exec('tasklist /FI "PID eq ' . $pid . '" /NH', $output, $return_var);
        if ($return_var !== 0 || empty($output[0])) {
            return false;
        }
        return stripos($output[0], (string)$pid) !== false && stripos($output[0], 'No tasks are running') === false;
    }
     $process_exists_cmd = "ps -p $pid -o pid= 2>/dev/null | grep -q $pid";
     $process_exists = (exec($process_exists_cmd, $output, $return_var) !== false) && $return_var === 0;
     return $process_exists;
 }

function terminateProcess($pid) {
    $pid = (int)$pid;
    if ($pid <= 0) {
        return false;
    }
    if ($this->isWindowsPlatform()) {
        exec('taskkill /PID ' . $pid . ' /F /T', $out, $ret);
        return $ret === 0;
    }
    exec("kill -TERM $pid 2>/dev/null", $out, $ret);
    return $ret === 0;
}

function getMcpPipeWorkingDir() {
    return dirname(__FILE__) . '/lib';
}

function stopMcpPipeProcess() {
    $pids = $this->getPidByName($this->mcpPipeProcessName, 1);
    if ($pids === null) {
        return true;
    }
    if (!is_array($pids)) {
        $pids = [(int)$pids];
    }
    $ok = true;
    foreach ($pids as $pid) {
        if ($this->isProcessRunning($pid)) {
            $terminated = $this->terminateProcess($pid);
            $ok = $ok && $terminated;
        }
    }
    return $ok;
}

  /**
    * Путь к mcp_config.json (лежит в рабочей папке mcp_pipe.py, т.е. в lib/).
    */
 function getMcpConfigPath() {
     return $this->getMcpPipeWorkingDir() . '/mcp_config.json';
 }

  /**
    * Гарантирует наличие рабочего lib/mcp_config.json.
    *
    * Файл создаётся только в install() модуля. Если модуль установили раньше
    * появления этой логики (или файл удалили), mcp_pipe.py стартует без
    * аргументов, load_config() возвращает {}, enabled = [] и процесс падает
    * с "No enabled mcpServers found in config" — мост не поднимается,
    * LLM не видит инструменты и отвечает «инструменты недоступны».
    * Здесь файл создаётся/чинится перед каждым запуском моста.
    *
    * @return bool true, если конфиг пригоден для запуска
    */
 function ensureMcpConfig() {
     $config_file_path = $this->getMcpConfigPath();

     if (is_file($config_file_path)) {
         $raw = @file_get_contents($config_file_path);
         $decoded = ($raw !== false) ? json_decode($raw, true) : null;
         $servers = (is_array($decoded) && isset($decoded['mcpServers'])) ? $decoded['mcpServers'] : null;
         // Непустой список серверов — трогаем только пустой/битый конфиг.
         if (is_array($servers) && count($servers) > 0) {
             return true;
         }
     }

     $config_content = json_encode([
         'mcpServers' => [
             'mcp' => [
                 'type' => 'stdio',
                 'command' => $this->python_bin,
                 'args' => ['-m', 'mcp-xiaozhi']
             ]
         ]
     ], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);

     if (@file_put_contents($config_file_path, $config_content)) {
         DebMes("mcp_config.json отсутствовал или был пуст — создан заново: " . $config_file_path, $this->name);
         return true;
     }

     DebMes("Не удалось создать mcp_config.json: " . $config_file_path . " — мост MCP не поднимется.", $this->name);
     return false;
 }

 /**
  * Читает список MCP-серверов из mcp_config.json (секция mcpServers).
  *
  * @return array имя => описание сервера
  */
 function getMcpServersConfig() {
     $this->ensureMcpConfig();
     $path = $this->getMcpConfigPath();
     if (!is_file($path)) {
         return [];
     }
     $raw = @file_get_contents($path);
     $decoded = ($raw !== false) ? json_decode($raw, true) : null;
     if (!is_array($decoded) || !isset($decoded['mcpServers']) || !is_array($decoded['mcpServers'])) {
         return [];
     }
     return $decoded['mcpServers'];
 }

 /**
  * Сохраняет список MCP-серверов в mcp_config.json.
  *
  * @return bool
  */
 function saveMcpServersConfig($servers) {
     if (!is_array($servers)) {
         return false;
     }
     $content = json_encode(
         ['mcpServers' => $servers],
         JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE
     );
     $result = @file_put_contents($this->getMcpConfigPath(), $content);
     return $result !== false;
 }

 /**
  * Файлы-кандидаты MCP-серверов в lib (для выбора в админке).
  *
  * Исключаем инфраструктуру самого моста: mcp_pipe.py — это транспорт,
  * mcp_class.py и mcp-xiaozhi.py — встроенный основной сервер модуля.
  *
  * @return array список имён файлов (*.py)
  */
 function listMcpServerFiles() {
     $excluded = ['mcp_pipe.py', 'mcp_class.py', 'mcp-xiaozhi.py'];
     $files = [];
     foreach ($this->listMcpLibPythonFiles() as $base) {
         if (in_array($base, $excluded, true)) {
             continue;
         }
         $files[] = $base;
     }
     natcasesort($files);
     return array_values($files);
 }

 /**
  * Все файлы *.py в lib модуля (включая инфраструктуру моста).
  *
  * Нужен для предупреждения при загрузке файла с уже существующим именем.
  *
  * @return array список имён файлов (*.py)
  */
 function listMcpLibPythonFiles() {
     $dir = $this->getMcpPipeWorkingDir();
     $files = [];
     foreach ((array)glob($dir . '/*.py') as $path) {
         $files[] = basename($path);
     }
     natcasesort($files);
     return array_values($files);
 }

 /**
  * Имена файлов, уже подключённых как MCP-серверы (по args в конфиге).
  *
  * Нужно, чтобы не показывать в выпадающем списке уже подключённые файлы
  * и не подключать один и тот же скрипт дважды.
  *
  * @param array|null $servers если null — читается из mcp_config.json
  * @return array список basename(*.py)
  */
 function getUsedMcpServerFiles($servers = null) {
     if ($servers === null) {
         $servers = $this->getMcpServersConfig();
     }
     $used = [];
     foreach ($servers as $entry) {
         $entry = is_array($entry) ? $entry : [];
         $args = (isset($entry['args']) && is_array($entry['args'])) ? $entry['args'] : [];
         foreach ($args as $arg) {
             $arg = (string)$arg;
             if (substr($arg, -3) === '.py') {
                 $used[basename($arg)] = true;
             }
         }
     }
     return array_keys($used);
 }

 /**
  * Занято ли имя MCP-сервера (проверка без учёта регистра).
  *
  * @param array  $servers список серверов из конфига
  * @param string $name    проверяемое имя
  * @return bool
  */
 function mcpServerNameExists($servers, $name) {
     foreach ((array)$servers as $key => $unused) {
         if (strcasecmp((string)$key, (string)$name) === 0) {
             return true;
         }
     }
     return false;
 }

 /**
  * Разбирает построчно переменные окружения сервера (KEY=значение).
  *
  * Пустые строки пропускаются. Строка без "=" или с недопустимым именем
  * переменной считается ошибкой ($ok = false).
  *
  * @param string $raw введённый текст
  * @param bool   $ok  false, если формат неверен
  * @return array ассоциативный массив KEY => значение (сохраняет порядок)
  */
 function parseMcpEnvInput($raw, &$ok) {
     $ok = true;
     $env = [];
     foreach (preg_split('/\r\n|\r|\n/', (string)$raw) as $line) {
         $line = trim($line);
         if ($line === '') {
             continue;
         }
         $pos = strpos($line, '=');
         if ($pos === false) {
             $ok = false;
             return [];
         }
         $key = trim(substr($line, 0, $pos));
         $value = trim(substr($line, $pos + 1));
         if (!preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/', $key)) {
             $ok = false;
             return [];
         }
         $env[$key] = $value;
     }
     return $env;
 }

 /**
  * Админ-страница «MCP-серверы»: список, добавление, удаление.
  */
 function search_mcpServer(&$out) {
     $servers = $this->getMcpServersConfig();

     $rows = [];
     foreach ($servers as $name => $entry) {
         $entry = is_array($entry) ? $entry : [];
         $args = (isset($entry['args']) && is_array($entry['args'])) ? $entry['args'] : [];
         $args_text = [];
         $script = '';
         foreach ($args as $arg) {
             $arg = (string)$arg;
             $args_text[] = $arg;
             if ($script === '' && substr($arg, -3) === '.py') {
                 $script = basename($arg);
             }
         }
         $is_main = ($name === $this->mainMcpServerName);
         $env_text = '';
         if (isset($entry['env']) && is_array($entry['env'])) {
             $env_parts = [];
             foreach ($entry['env'] as $env_key => $env_val) {
                 $env_parts[] = $env_key . '=' . $env_val;
             }
             $env_text = implode('; ', $env_parts);
         }
         $rows[] = [
             'NAME'        => $name,
             'IS_MAIN'     => $is_main ? 1 : 0,
             'CAN_DELETE'  => $is_main ? 0 : 1,
             'SCRIPT'      => $script,
             'ARGS'        => implode(' ', $args_text),
             'ENV'         => $env_text,
             'COMMAND'     => isset($entry['command']) ? $entry['command'] : '',
             'DISABLED'    => !empty($entry['disabled']) ? 1 : 0,
         ];
     }
     $out['RESULT'] = $rows;
     $out['MAIN_SERVER_NAME'] = $this->mainMcpServerName;

     // Имена файлов в lib — для предупреждения о подмене при загрузке.
     $out['LIB_FILES_JSON'] = json_encode(array_values($this->listMcpLibPythonFiles()), JSON_UNESCAPED_UNICODE);

     // Список файлов для выпадающего выбора: только ещё не подключённые.
     $used = $this->getUsedMcpServerFiles($servers);
     $server_files = [];
     foreach ($this->listMcpServerFiles() as $file) {
         if (in_array($file, $used, true)) {
             continue;
         }
         $server_files[] = ['FILE' => $file];
     }
     $out['SERVER_FILES'] = $server_files;

     $out['OK'] = gr('ok') ? 1 : 0;

     $errors = [
         'name'     => 'Укажите название MCP-сервера.',
         'invalid'  => 'Название может содержать только латинские буквы, цифры, дефис и подчёркивание. Русские буквы недопустимы.',
         'reserved' => 'Сервер «' . $this->mainMcpServerName . '» — основной, его нельзя удалить или переопределить.',
         'exists'   => 'Сервер с таким названием уже есть в списке.',
         'nofile'   => 'Файл не выбран.',
         'file'     => 'Выбранный файл не найден в папке lib модуля.',
         'used'     => 'Этот файл уже подключён как MCP-сервер.',
         'env'      => 'Переменные окружения задаются по одной в строке в формате KEY=значение (латинские буквы, цифры, подчёркивание).',
         'upload'   => 'Не удалось загрузить файл (слишком большой или ошибка загрузки).',
         'py'       => 'Можно загружать только файлы с расширением .py.',
         'copy'     => 'Не удалось скопировать файл в папку lib модуля. Проверьте права.',
         'save'     => 'Не удалось записать mcp_config.json. Проверьте права на папку lib модуля.',
     ];
     $err = trim((string)gr('err'));
     $out['ERR_MESSAGE'] = isset($errors[$err]) ? $errors[$err] : '';
 }

 /**
  * Добавляет MCP-сервер в mcp_config.json и перезапускает сервис.
  */
 function add_mcpServer(&$out) {
     $name = trim((string)gr('server_name'));
     $file = trim((string)gr('server_file'));

     $env = $this->parseMcpEnvInput((string)gr('server_env'), $env_ok);
     if (!$env_ok) {
         $this->redirect("?view_mode=search_mcpServer&err=env");
         return;
     }

     // Файл должен быть выбран из списка доступных файлов папки lib.
     if ($file === '') {
         $this->redirect("?view_mode=search_mcpServer&err=nofile");
         return;
     }
     if (!in_array($file, $this->listMcpServerFiles(), true)) {
         $this->redirect("?view_mode=search_mcpServer&err=file");
         return;
     }

     if ($name === '') {
         $this->redirect("?view_mode=search_mcpServer&err=name");
         return;
     }
     if (!preg_match('/^[A-Za-z0-9_-]+$/', $name)) {
         $this->redirect("?view_mode=search_mcpServer&err=invalid");
         return;
     }
     if (strcasecmp($name, $this->mainMcpServerName) === 0) {
         $this->redirect("?view_mode=search_mcpServer&err=reserved");
         return;
     }

     $servers = $this->getMcpServersConfig();
     if ($this->mcpServerNameExists($servers, $name)) {
         $this->redirect("?view_mode=search_mcpServer&err=exists");
         return;
     }
     if (in_array($file, $this->getUsedMcpServerFiles($servers), true)) {
         $this->redirect("?view_mode=search_mcpServer&err=used");
         return;
     }

     $servers[$name] = [
         'type'    => 'stdio',
         'command' => $this->python_bin,
         'args'    => [$this->getMcpPipeWorkingDir() . '/' . $file],
     ];
     if (!empty($env)) {
         $servers[$name]['env'] = $env;
     }

     if (!$this->saveMcpServersConfig($servers)) {
         $this->redirect("?view_mode=search_mcpServer&err=save");
         return;
     }

     $this->restartMcpPipeService();
     $this->WriteLog("Добавлен MCP-сервер '{$name}' ({$file}), сервис перезапущен");
     $this->redirect("?view_mode=search_mcpServer&ok=1");
 }

 /**
  * Загружает файл MCP-сервера (*.py) в папку lib модуля и сразу подключает его.
  *
  * Файл берётся с компьютера пользователя: сохраняем его рядом с остальными
  * серверами (lib) и добавляем запись в mcp_config.json, затем перезапускаем
  * мост. Если файл с таким именем уже есть в lib — он заменяется, а для уже
  * подключённого файла новая запись в конфиге не создаётся.
  */
 function upload_mcpServer(&$out) {
     $name = trim((string)gr('server_name'));
     $upload = isset($_FILES['server_upload']) ? $_FILES['server_upload'] : null;

     $env = $this->parseMcpEnvInput((string)gr('server_env'), $env_ok);
     if (!$env_ok) {
         $this->redirect("?view_mode=search_mcpServer&err=env");
         return;
     }

     if (!is_array($upload) || !isset($upload['tmp_name'])
         || (int)$upload['error'] === UPLOAD_ERR_NO_FILE) {
         $this->redirect("?view_mode=search_mcpServer&err=nofile");
         return;
     }
     if ((int)$upload['error'] !== UPLOAD_ERR_OK) {
         $this->redirect("?view_mode=search_mcpServer&err=upload");
         return;
     }

     // Лимит размера — с запасом для скрипта сервера.
     $max_size = 2 * 1024 * 1024;
     if (!is_uploaded_file($upload['tmp_name']) || (int)$upload['size'] <= 0) {
         $this->redirect("?view_mode=search_mcpServer&err=nofile");
         return;
     }
     if ((int)$upload['size'] > $max_size) {
         $this->redirect("?view_mode=search_mcpServer&err=upload");
         return;
     }

     $original = basename((string)$upload['name']);
     if (strtolower((string)pathinfo($original, PATHINFO_EXTENSION)) !== 'py') {
         $this->redirect("?view_mode=search_mcpServer&err=py");
         return;
     }

     // Безопасное имя файла в папке lib (без слешей, кавычек и спецсимволов).
     $file = preg_replace('/[^A-Za-z0-9_.-]/', '_', $original);
     $file = ltrim($file, '.');
     if ($file === '' || strtolower((string)pathinfo($file, PATHINFO_EXTENSION)) !== 'py') {
         $file = 'mcp_server_' . substr(md5($original . microtime()), 0, 8) . '.py';
     }

     $servers = $this->getMcpServersConfig();
     // Файл уже подключён как сервер — тогда просто подменяем его содержимое.
     $already_used = in_array($file, $this->getUsedMcpServerFiles($servers), true);

     if (!$already_used) {
         // Имя сервера: из поля, иначе из имени файла без расширения.
         if ($name === '') {
             $name = preg_replace('/[^A-Za-z0-9_-]+/', '_', (string)pathinfo($original, PATHINFO_FILENAME));
             $name = trim($name, '_-');
         }
         if ($name === '') {
             $this->redirect("?view_mode=search_mcpServer&err=name");
             return;
         }
         if (!preg_match('/^[A-Za-z0-9_-]+$/', $name)) {
             $this->redirect("?view_mode=search_mcpServer&err=invalid");
             return;
         }
         if (strcasecmp($name, $this->mainMcpServerName) === 0) {
             $this->redirect("?view_mode=search_mcpServer&err=reserved");
             return;
         }
         if ($this->mcpServerNameExists($servers, $name)) {
             $this->redirect("?view_mode=search_mcpServer&err=exists");
             return;
         }
     }

     // Файл с таким именем в lib подменяем (предупреждение показано в форме).
     $target_path = $this->getMcpPipeWorkingDir() . '/' . $file;
     if (file_exists($target_path) && !@unlink($target_path)) {
         $this->redirect("?view_mode=search_mcpServer&err=copy");
         return;
     }
     if (!@move_uploaded_file($upload['tmp_name'], $target_path)) {
         $this->redirect("?view_mode=search_mcpServer&err=copy");
         return;
     }
     @chmod($target_path, 0644);

     if (!$already_used) {
         $servers[$name] = [
             'type'    => 'stdio',
             'command' => $this->python_bin,
             'args'    => [$target_path],
         ];
         if (!empty($env)) {
             $servers[$name]['env'] = $env;
         }

         if (!$this->saveMcpServersConfig($servers)) {
             $this->redirect("?view_mode=search_mcpServer&err=save");
             return;
         }
     }

     $this->restartMcpPipeService();
     $this->WriteLog("Загружен и подключён MCP-сервер '{$name}' ({$file}), сервис перезапущен");
     $this->redirect("?view_mode=search_mcpServer&ok=1");
 }

 /**
  * Удаляет MCP-сервер из mcp_config.json (кроме основного) и перезапускает сервис.
  */
 function delete_mcpServer(&$out) {
     $name = trim((string)gr('server_name'));
     if ($name === '') {
         $this->redirect("?view_mode=search_mcpServer");
         return;
     }
     // Основной сервер модуля удалять запрещено.
     if ($name === $this->mainMcpServerName) {
         $this->redirect("?view_mode=search_mcpServer&err=reserved");
         return;
     }

     $servers = $this->getMcpServersConfig();
     if (!isset($servers[$name])) {
         $this->redirect("?view_mode=search_mcpServer");
         return;
     }

     unset($servers[$name]);
     if (!$this->saveMcpServersConfig($servers)) {
         $this->redirect("?view_mode=search_mcpServer&err=save");
         return;
     }
     $this->restartMcpPipeService();
     $this->WriteLog("Удалён MCP-сервер '{$name}', сервис перезапущен");
     $this->redirect("?view_mode=search_mcpServer&ok=1");
 }

 /**
  * Перезапускает мост mcp_pipe.py, чтобы применился изменённый набор серверов.
  *
  * Останавливаем текущий процесс и запускаем заново с тем же endpoint.
  * Если процесс не поднимется — cycle_mcp.php подстрахует и перезапустит его.
  */
 function restartMcpPipeService() {
     $this->getConfig();
     $endpoint = isset($this->config['MCP_ENDPOINT']) ? trim($this->config['MCP_ENDPOINT']) : '';

     if ($this->getPidByName($this->mcpPipeProcessName) !== null) {
         $this->stopMcpPipeProcess();
         // Дать процессу время корректно завершиться перед новым стартом.
         sleep(1);
     }

     if ($endpoint !== '') {
         $this->startMcpPipeProcess($endpoint);
     }
 }

 function startMcpPipeProcess($mcpEndpoint = '') {
    $workingDir = $this->getMcpPipeWorkingDir();
    $scriptName = 'mcp_pipe.py';
    // Без рабочего конфига mcp_pipe.py завершается сразу после старта.
    $this->ensureMcpConfig();
    if ($this->isWindowsPlatform()) {
        $pythonExe = str_replace('/', '\\', $this->python_bin);
        $workDir = str_replace('/', '\\', $workingDir);
        $endpoint = str_replace("'", "''", $mcpEndpoint);
        // Вывод процесса пишем в логи: без них причины падений на Windows не видны
        // (на Linux вывод уходит в /tmp/mcp_pipe_out.log).
        $logDir = rtrim(str_replace('/', '\\', sys_get_temp_dir()), '\\');
        $outLog = $logDir . '\\mcp_pipe_out.log';
        $errLog = $logDir . '\\mcp_pipe_err.log';
        // Запускаем отсоединённый процесс через PowerShell Start-Process.
        // Значения передаются как данные (без хрупкого экранирования cmd.exe).
        $psScript = "\$ErrorActionPreference = 'Stop'; \$ProgressPreference = 'SilentlyContinue'; "
            . "\$env:MCP_ENDPOINT = '" . $endpoint . "'; "
            . "Remove-Item -Force -ErrorAction SilentlyContinue '" . str_replace("'", "''", $outLog) . "', '" . str_replace("'", "''", $errLog) . "'; "
            . "Start-Process -FilePath '" . str_replace("'", "''", $pythonExe) . "' "
            . "-ArgumentList '" . str_replace("'", "''", $scriptName) . "' "
            . "-WorkingDirectory '" . str_replace("'", "''", $workDir) . "' "
            . "-WindowStyle Hidden "
            . "-RedirectStandardOutput '" . str_replace("'", "''", $outLog) . "' "
            . "-RedirectStandardError '" . str_replace("'", "''", $errLog) . "'";
        $encoded = $this->encodePowerShellCommand($psScript);
        $cmd = 'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' . $encoded;
        $output = [];
        $return_var = -1;
        exec($cmd, $output, $return_var);
        // Проверяем, что процесс действительно поднялся
        sleep(2);
        if ($this->getPidByName($this->mcpPipeProcessName) !== null) {
            return true;
        }
        DebMes("Не удалось запустить mcp_pipe.py (код " . $return_var . "). Лог ошибок: " . $errLog, $this->name);
        return false;
    }
    $cmd = '(cd ' . escapeshellarg($workingDir) . ' && MCP_ENDPOINT=' . escapeshellarg($mcpEndpoint) . ' nohup ' . escapeshellarg($this->python_bin) . ' ' . escapeshellarg($scriptName) . ' > /tmp/mcp_pipe_out.log 2>&1 &)';
    exec($cmd, $output, $return_var);
    return $return_var === 0;
}

function ensureSingleMcpPipeProcess() {
    $pids = $this->getPidByName($this->mcpPipeProcessName, 1);
    if ($pids === null) {
        return null;
    }
    if (!is_array($pids)) {
        return (int)$pids;
    }
    $total = count($pids);
    if ($total <= 1) {
        return (int)$pids[0];
    }
    for ($i = 0; $i < $total - 1; $i++) {
        $this->terminateProcess($pids[$i]);
    }
    return (int)$pids[$total - 1];
}

 /**
   *
   * Запись отладочной информации в DebMes-лог модуля.
   *
   */
 function WriteLog($msg) {
     if ($this->debug) {
         DebMes($msg, $this->name);
     }
 }
   
 function processCycle() {
     $this->getConfig();
	 $this->schedule();
     //to-do
 }
 
/**
* Install
*
* Module installation routine
*
* @access private
*/
 function install($data='')
 {
    parent::install();


   $command = $this->getSystemPythonCommand() . ' 2>&1';
    $output = [];
    $return_var = 0;
    exec($command, $output, $return_var);
    DebMes("PATH: " . implode("\n", $output), $this->name);
   if ($return_var !== 0 || empty($output[0])) {
       DebMes("Python interpreter not found in PATH.", $this->name);
       return;
   }

    // 1. Создание виртуального окружения
   $python_cmd = trim($output[0]);
   $command = escapeshellarg($python_cmd) . ' -m venv ' . escapeshellarg($this->venv_path);
    DebMes($command, $this->name);
    $output = [];
    $return_var = 0;
    exec($command, $output, $return_var);

    if ($return_var !== 0) {
        DebMes("Проверьте права на папку. Ошибка создания виртуального окружения: " . $output[0], $this->name);
    } else {
        DebMes("Создание виртуального окружения выполнено.", $this->name);

        // Создаем конфигурационный файл mcp_config.json (единая точка — ensureMcpConfig)
        if ($this->ensureMcpConfig()) {
            DebMes("Файл конфигурации mcp_config.json готов.", $this->name);
        } else {
            DebMes("Ошибка при создании файла конфигурации mcp_config.json.", $this->name);
        }

        // Таблица сцен (для уже установленных модулей создаётся без переустановки)
        $this->ensureScenesTable();

        // Таблица планировщика (создаётся/дополняется без переустановки)
        $this->ensureScheduleTable();
		
        // 2. Установка зависимостей через pip из виртуального окружения
        $requirements_path = escapeshellarg(dirname(__FILE__) . '/lib/requirements.txt');
        if ($this->isWindowsPlatform()) {
            $command = '"' . $this->pip_bin . '" install -r ' . $requirements_path;
        } else {
            $command = 'nohup ' . escapeshellarg($this->pip_bin) . ' install -r ' . $requirements_path . ' > /tmp/pip_install.log 2>&1 &';
        }
        $output = [];
        $return_var = 0;
        exec($command, $output, $return_var);

        if ($return_var !== 0) {
            DebMes("Ошибка установки зависимостей.", $this->name);
        } else {
            DebMes("Зависимости успешно установлены.", $this->name);
        }
    }

// 3. Запуск скриптов также через python из виртуального окружения
//$command = $python_bin . ' ' . escapeshellarg(dirname(__FILE__) . '/lib/your_script.py');
//exec($command, $output, $return_var);

 }

 /**
 * Путь к бинарнику внутри произвольного виртуального окружения.
 */
 function getVenvBinaryAt($venv_path, $unixName, $windowsName) {
    if ($this->isWindowsPlatform()) {
        return $venv_path . '/Scripts/' . $windowsName . '.exe';
    }
    return $venv_path . '/bin/' . $unixName;
 }

 /**
 * Удаляет ТОЛЬКО виртуальное окружение модуля (.venv или .venv_new).
 * Страховка: возвращаем false и ничего не делаем для любых других путей.
 */
 function removeVenvDirectory($path) {
    $base = basename(rtrim(str_replace('\\', '/', (string)$path), '/'));
    if ($base !== '.venv' && $base !== '.venv_new') {
        return false;
    }
    if (!is_dir($path)) {
        return true;
    }
    if ($this->isWindowsPlatform()) {
        $command = 'rmdir /S /Q "' . str_replace('/', '\\', $path) . '" 2>&1';
    } else {
        $command = 'rm -rf ' . escapeshellarg($path) . ' 2>&1';
    }
    $output = [];
    $return_var = 0;
    @exec($command, $output, $return_var);
    return ($return_var === 0 && !is_dir($path));
 }

 /**
 * Версия интерпретатора Python: [major, minor, patch] либо null.
 */
 function getPythonVersion($python_path) {
    if (!$python_path || !file_exists($python_path)) {
        return null;
    }
    $output = [];
    $return_var = 0;
    @exec(escapeshellarg($python_path) . ' --version 2>&1', $output, $return_var);
    if ($return_var !== 0) {
        return null;
    }
    if (!preg_match('/(\d+)\.(\d+)(?:\.(\d+))?/', implode(' ', $output), $m)) {
        return null;
    }
    return [intval($m[1]), intval($m[2]), isset($m[3]) ? intval($m[3]) : 0];
 }

 /**
 * Ищет доступный интерпретатор Python не ниже $min_version. Возвращает путь или null.
 */
 function findSuitablePython($min_version = [3, 10]) {
    $candidates = [];
    if ($this->isWindowsPlatform()) {
        $found = [];
        @exec('where python 2>NUL', $found);
        foreach ($found as $path) {
            $path = trim($path);
            if ($path !== '') {
                $candidates[] = $path;
            }
        }
    } else {
        foreach (['python3', 'python3.12', 'python3.13', 'python3.11', 'python3.10'] as $name) {
            $found = [];
            @exec('which ' . escapeshellarg($name) . ' 2>/dev/null', $found);
            if (!empty($found[0])) {
                $candidates[] = trim($found[0]);
            }
        }
    }
    $best = null;
    $best_version = null;
    foreach (array_unique($candidates) as $path) {
        $version = $this->getPythonVersion($path);
        if ($version === null) {
            continue;
        }
        $version_str = $version[0] . '.' . $version[1] . '.' . $version[2];
        if (version_compare($version_str, $min_version[0] . '.' . $min_version[1], '<')) {
            continue;
        }
        if ($best_version === null || version_compare($version_str, $best_version, '>')) {
            $best = $path;
            $best_version = $version_str;
        }
    }
    return $best;
 }

 /**
 * Автоисправление окружения Python: интерпретатор, виртуальное окружение, зависимости.
 *
 * Возвращает ['ok' => bool, 'steps' => [['label' => .., 'ok' => .., 'text' => ..]]].
 */
 function fixPythonEnvironment() {
    // Кнопка может отработать дольше веб-таймаута — не прерываем скрипт,
    // даже если браузер уже отключился.
    @set_time_limit(0);
    @ignore_user_abort(true);
    $steps = [];
    $requirements_path = dirname(__FILE__) . '/lib/requirements.txt';

    // 1. Подбираем интерпретатор не ниже 3.10.
    $python_cmd = $this->findSuitablePython();
    if (!$python_cmd) {
        $steps[] = ['label' => 'Интерпретатор Python', 'ok' => false,
            'text' => 'В системе не найден Python 3.10 или новее. Установите его вручную.'];
        return ['ok' => false, 'steps' => $steps];
    }
    $version = $this->getPythonVersion($python_cmd);
    $steps[] = ['label' => 'Интерпретатор Python', 'ok' => true,
        'text' => $python_cmd . ' (версия ' . implode('.', $version) . ')'];

    // 2. Виртуальное окружение: пересоздаём, если оно отсутствует/битое или на старом Python.
    $env_ok = is_dir($this->venv_path) && file_exists($this->python_bin) && file_exists($this->pip_bin);
    $env_version = $env_ok ? $this->getPythonVersion($this->python_bin) : null;
    $env_old = $env_ok && ($env_version === null || version_compare($env_version[0] . '.' . $env_version[1], '3.10', '<'));

    if (!$env_ok || $env_old) {
        $reason = !$env_ok ? 'окружение отсутствует или битое'
            : 'в окружении устаревший Python ' . implode('.', $env_version);
        $tmp_path = $this->venv_path . '_new';
        $this->removeVenvDirectory($tmp_path);

        // Сначала создаём в временной папке: старое окружение не трогаем,
        // пока новое не окажется готовым.
        $output = [];
        $return_var = 0;
        @exec(escapeshellarg($python_cmd) . ' -m venv ' . escapeshellarg($tmp_path), $output, $return_var);
        $tmp_python = $this->getVenvBinaryAt($tmp_path, 'python3', 'python');
        if ($return_var !== 0 || !file_exists($tmp_python)) {
            $this->removeVenvDirectory($tmp_path);
            $steps[] = ['label' => 'Виртуальное окружение', 'ok' => false,
                'text' => 'Не удалось создать окружение (' . $reason . ')'
                    . ($output ? ': ' . implode(' ', $output) : '') . '. Старое окружение сохранено.'];
            return ['ok' => false, 'steps' => $steps];
        }
        if (!$this->removeVenvDirectory($this->venv_path)) {
            $this->removeVenvDirectory($tmp_path);
            $steps[] = ['label' => 'Виртуальное окружение', 'ok' => false,
                'text' => 'Не удалось удалить старое окружение. Проверьте права на папку модуля.'];
            return ['ok' => false, 'steps' => $steps];
        }
        @rename($tmp_path, $this->venv_path);
        if (!is_dir($this->venv_path)) {
            $steps[] = ['label' => 'Виртуальное окружение', 'ok' => false,
                'text' => 'Не удалось заменить окружение. Проверьте права на папку модуля.'];
            return ['ok' => false, 'steps' => $steps];
        }
        $this->python_bin = $this->getVenvBinary('python3', 'python');
        $this->pip_bin = $this->getVenvBinary('pip3', 'pip');
        $steps[] = ['label' => 'Виртуальное окружение', 'ok' => true,
            'text' => 'Создано заново (' . $reason . '): ' . $this->venv_path];
    } else {
        $steps[] = ['label' => 'Виртуальное окружение', 'ok' => true,
            'text' => 'В порядке, пересоздание не требовалось (' . $this->venv_path . ')'];
    }

    // 3. Права на скрипты окружения: без бита исполнения pip не запускается, и проверка
    // не может получить список пакетов (файл существует — file_exists это не видит).
    if (!$this->isWindowsPlatform()) {
        $pip_was_executable = is_executable($this->pip_bin);
        $output = [];
        $return_var = 0;
        @exec('find ' . escapeshellarg($this->venv_path . '/bin')
            . ' -type f -exec chmod 755 {} + 2>&1', $output, $return_var);
        $pip_ok = ($return_var === 0) && is_executable($this->pip_bin);
        $steps[] = ['label' => 'Права на скрипты окружения', 'ok' => $pip_ok,
            'text' => $pip_ok
                ? ($pip_was_executable
                    ? 'В порядке (' . basename($this->venv_path) . '/bin/*)'
                    : 'Восстановлен бит исполнения для ' . basename($this->venv_path) . '/bin/*')
                : 'Права не восстановлены: ' . $this->pip_bin . ($output ? ': ' . implode(' ', $output) : '')];
    }

    // 4. Зависимости из requirements.txt.
    if (!file_exists($requirements_path)) {
        $steps[] = ['label' => 'Зависимости', 'ok' => false,
            'text' => 'Файл lib/requirements.txt не найден.'];
        return ['ok' => false, 'steps' => $steps];
    }
    $output = [];
    $return_var = 0;
    // Ставим зависимости через «python -m pip», а не через скрипт pip3: после
    // переименования окружения в его скриптах остаётся старый путь в shebang.
    @exec(escapeshellarg($this->python_bin) . ' -m pip install -r ' . escapeshellarg($requirements_path) . ' 2>&1', $output, $return_var);
    if ($return_var !== 0) {
        $tail = implode(' ', array_slice($output, -4));
        $steps[] = ['label' => 'Зависимости', 'ok' => false,
            'text' => 'pip завершился с ошибкой: ' . $tail];
        return ['ok' => false, 'steps' => $steps];
    }
    $steps[] = ['label' => 'Зависимости', 'ok' => true, 'text' => 'Установлены из lib/requirements.txt'];

    // 5. Перезапускаем мост, чтобы он подхватил новое окружение.
    setGlobal('cycle_mcpExit', true);
    $this->stopMcpPipeProcess();
    $steps[] = ['label' => 'Мост MCP', 'ok' => true, 'text' => 'Перезапущен, подхватит новое окружение'];

    // Итог — по всем шагам, а не «true по умолчанию».
    $ok = true;
    foreach ($steps as $step) {
        if (empty($step['ok'])) {
            $ok = false;
            break;
        }
    }
    return ['ok' => $ok, 'steps' => $steps];
 }

 /**
* Uninstall
*
* Module uninstall routine
*
* @access public
*/
 function uninstall() {
     SQLExec('DROP TABLE IF EXISTS mcp_devices');
     SQLExec('DROP TABLE IF EXISTS mcp_schedule');
     SQLExec('DROP TABLE IF EXISTS mcp_scenes');
     parent::uninstall();
	 
	 if (is_dir($this->venv_path)) {
        if ($this->isWindowsPlatform()) {
            $command = 'rmdir /S /Q "' . str_replace('/', '\\', $this->venv_path) . '" 2>&1';
        } else {
            $command = 'rm -rf ' . escapeshellarg($this->venv_path) . ' 2>&1';
        }
         $output = [];
         $return_var = 0;
         exec($command, $output, $return_var);
    
         if ($return_var === 0) {
            DebMes("Старое виртуальное окружение удалено.", $this->name);
         } else {
             $error_msg = !empty($output) ? $output[0] : 'Ошибка при удалении виртуального окружения';
             DebMes("Ошибка удаления виртуального окружения: " . $error_msg, $this->name);
         }
     } else {
         DebMes("Директория виртуального окружения не существует, пропускаем удаление.", $this->name);
     }
 }
/**
* dbInstall
*
* Database installation routine
*
* @access private
*/
 function dbInstall($data) {
     /*
     mcp_devices - 
     */
     $data = <<<EOD
         mcp_devices: ID int(10) unsigned NOT NULL auto_increment
         mcp_devices: TITLE varchar(255) NOT NULL DEFAULT ''
         mcp_devices: ALIACE varchar(255) NOT NULL DEFAULT ''
         mcp_devices: TYPE varchar(100) NOT NULL DEFAULT ''
         mcp_devices: ROOM varchar(100) NOT NULL DEFAULT ''
         mcp_devices: DESCRIPTION varchar(100) NOT NULL DEFAULT ''
         mcp_devices: MANUFACTURER varchar(100) NOT NULL DEFAULT ''
         mcp_devices: MODEL varchar(100) NOT NULL DEFAULT ''
         mcp_devices: SW_VERSION varchar(100) NOT NULL DEFAULT ''
         mcp_devices: HW_VERSION varchar(100) NOT NULL DEFAULT ''
         mcp_devices: MAC varchar(64) NOT NULL DEFAULT ''
         mcp_devices: TRAITS text
         mcp_devices: CONFIG text

         mcp_schedule: ID int(10) unsigned NOT NULL auto_increment
         mcp_schedule: TASK_ID VARCHAR(255) NOT NULL DEFAULT ''
         mcp_schedule: TITLE varchar(255) NOT NULL DEFAULT ''
         mcp_schedule: ENABLED tinyint(1) NOT NULL DEFAULT 0
         mcp_schedule: SCHEDULE_TIME varchar(10) NOT NULL DEFAULT ''
         mcp_schedule: SCHEDULE_DAYS varchar(255) NOT NULL DEFAULT ''
         mcp_schedule: SCHEDULE_DATE date DEFAULT NULL
         mcp_schedule: ACTION_TYPE varchar(50) NOT NULL DEFAULT ''
         mcp_schedule: ACTION_TARGET varchar(255) NOT NULL DEFAULT ''
         mcp_schedule: ACTION_STATE varchar(100) NOT NULL DEFAULT ''
         mcp_schedule: ACTION_DATA text
         mcp_schedule: LAST_RUN datetime DEFAULT NULL
         mcp_schedule: CREATED datetime NOT NULL DEFAULT CURRENT_TIMESTAMP
         mcp_schedule: UPDATED timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP

         mcp_scenes: ID int(10) unsigned NOT NULL auto_increment
         mcp_scenes: TITLE varchar(255) NOT NULL DEFAULT ''
         mcp_scenes: ALIACE varchar(255) NOT NULL DEFAULT ''
         mcp_scenes: ENABLED tinyint(1) NOT NULL DEFAULT 1
         mcp_scenes: ACTIONS text
         mcp_scenes: CREATED datetime NOT NULL DEFAULT CURRENT_TIMESTAMP
         mcp_scenes: UPDATED timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
EOD;
     parent::dbInstall($data);
 }
// --------------------------------------------------------------------
}
/*
*
* TW9kdWxlIGNyZWF0ZWQgSmFuIDA3LCAyMDI2IHVzaW5nIFNlcmdlIEouIHdpemFyZCAoQWN0aXZlVW5pdCBJbmMgd3d3LmFjdGl2ZXVuaXQuY29tKQ==
*
*/
