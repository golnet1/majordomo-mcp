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
     $out['VERSION']='1.7';
     $out['DATA_V']='15.10.26';
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
 
     if ($this->view_mode=='update_settings') {

        $this->config['MCP_ENDPOINT'] = gr('mcp_endpoint');
        $this->config['VIEW_STYLE'] = gr('view_style');
		$this->config['CHECK_PYTHON'] = gr('check_python');
        $this->config['LOG_DEBMES'] = gr('log_debmes');
        $this->config['LOG_PING']   = gr('log_ping');
		
		setGlobal('cycle_mcpExit', true);

        $this->saveConfig();
		
		$current_pid = $this->getPidByName($this->mcpPipeProcessName);
        if ($current_pid !== null) {
            $this->stopMcpPipeProcess();
        }
		
        $this->redirect("?");
     }
     if (isset($this->data_source) && !$_GET['data_source'] && !$_POST['data_source']) {
         $out['SET_DATASOURCE']=1;
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
         }

         echo 'OK';
     }
// $this->admin($out);
 }
 /**
 * mcp_devices search
 *
 * @access public
 */
 function search_mcpDevice(&$out) {
     require(dirname(__FILE__).'/device_search.inc.php');
 }

 function add_mcpDevice(&$out) {
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
 
 function propertySetHandle($object, $property, $value) {
     $this->getConfig();
      $table='mcp_devices';
      $properties=SQLSelect("SELECT ID FROM $table WHERE LINKED_OBJECT LIKE '".DBSafe($object)."' AND LINKED_PROPERTY LIKE '".DBSafe($property)."'");
      $total=count($properties);
      if ($total) {
          for($i=0;$i<$total;$i++) {
          //to-do
          }
      }
 }
 
/* function schedule() {
     $this->getConfig();
     $properties=SQLSelect("SELECT ID,SCHEDULE_TIME FROM mcp_schedule WHERE SCHEDULE_TIME LIKE '".date("H:i")."'");
     $total=count($properties);
     if ($total) {
         for($i=0;$i<$total;$i++) {
             $this->setProperty($properties[$i]['ID'], $value);
         }
     }
 }*/
 
 function schedule() {
     $this->getConfig();
     $currentTime = date("H:i");
     $properties = SQLSelect("SELECT * FROM mcp_schedule WHERE SCHEDULE_TIME LIKE '" . $currentTime . "' AND ENABLED = 1");
     $total = count($properties);
     if ($total) {
         for($i = 0; $i < $total; $i++) {
             $task = $properties[$i];
			 
             // Выполняем действие в зависимости от типа
             if ($task['ACTION_TYPE'] == 'device') {
                 $deviceName = $task['ACTION_TARGET'];
                 $actionState = $task['ACTION_STATE'];
                
                 // Нормализуем состояние для определения типа команды
                 $isOnAction = in_array(strtolower($actionState), ["включи", "включить", "on", "1", "да"]);
                 $value = $isOnAction ? "1" : "0";
                
                 // Найти объект и свойство для устройства
                 $deviceRecord = SQLSelectOne("SELECT * FROM mcp_devices WHERE TITLE = '" . DBSafe($deviceName) . "'");
                 $linkedObject = $deviceName;
                 $linkedProperty = "status";
                
                 if ($deviceRecord && $deviceRecord['TRAITS']) {
                     $traits = json_decode($deviceRecord['TRAITS'], true);
                     foreach ($traits as $trait) {
                         if (isset($trait['linked_object']) && isset($trait['linked_property'])) {
                             $linkedObject = $trait['linked_object'];
                             $linkedProperty = $trait['linked_property'];
                             break;
                         }
                     }
                 }
                
                 // Выполнить команду
                 setGlobal($linkedObject . '.' . $linkedProperty, $value);
             }
            
             // Проверяем, одноразовое ли задание
             $scheduleDays = json_decode($task['SCHEDULE_DAYS'], true);
             if (!$scheduleDays) {
                 $scheduleDays = explode(',', $task['SCHEDULE_DAYS']);
             }
            
             if (in_array('once', $scheduleDays)) {
                 // Удаляем одноразовое задание после выполнения
                 SQLExec("DELETE FROM mcp_schedule WHERE ID = " . $task['ID']);
             }
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

function startMcpPipeProcess($mcpEndpoint = '') {
    $workingDir = $this->getMcpPipeWorkingDir();
    $scriptName = 'mcp_pipe.py';
    if ($this->isWindowsPlatform()) {
        $pythonExe = str_replace('/', '\\', $this->python_bin);
        $workDir = str_replace('/', '\\', $workingDir);
        $endpoint = str_replace("'", "''", $mcpEndpoint);
        // Запускаем отсоединённый процесс через PowerShell Start-Process.
        // Значения передаются как данные (без хрупкого экранирования cmd.exe).
        $psScript = "\$ErrorActionPreference = 'Stop'; \$ProgressPreference = 'SilentlyContinue'; "
            . "\$env:MCP_ENDPOINT = '" . $endpoint . "'; "
            . "Start-Process -FilePath '" . str_replace("'", "''", $pythonExe) . "' "
            . "-ArgumentList '" . str_replace("'", "''", $scriptName) . "' "
            . "-WorkingDirectory '" . str_replace("'", "''", $workDir) . "' "
            . "-WindowStyle Hidden";
        $encoded = $this->encodePowerShellCommand($psScript);
        $cmd = 'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' . $encoded;
        $output = [];
        $return_var = -1;
        exec($cmd, $output, $return_var);
        // Проверяем, что процесс действительно поднялся
        sleep(2);
        return $this->getPidByName($this->mcpPipeProcessName) !== null;
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
		
		$python_venv_path = $this->python_bin;

        // Создаем конфигурационный файл mcp_config.json
        $config_content = json_encode([
            'mcpServers' => [
                'mcp' => [
                    'type' => 'stdio',
                    'command' => $python_venv_path,
                    'args' => ['-m', 'mcp-xiaozhi']
                ]
            ]
        ], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);

        $config_file_path = dirname(__FILE__) . '/lib/mcp_config.json';
        
        if (file_put_contents($config_file_path, $config_content)) {
            DebMes("Файл конфигурации mcp_config.json создан успешно.", $this->name);
        } else {
            DebMes("Ошибка при создании файла конфигурации mcp_config.json.", $this->name);
        }
		
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
* Uninstall
*
* Module uninstall routine
*
* @access public
*/
 function uninstall() {
     SQLExec('DROP TABLE IF EXISTS mcp_devices');
     SQLExec('DROP TABLE IF EXISTS mcp_schedule');
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
         mcp_devices: TRAITS text
         mcp_devices: CONFIG text

         mcp_schedule: ID int(10) unsigned NOT NULL auto_increment
         mcp_schedule: TASK_ID VARCHAR(255) NOT NULL DEFAULT ''
         mcp_schedule: TITLE varchar(255) NOT NULL DEFAULT ''
         mcp_schedule: ENABLED tinyint(1) NOT NULL DEFAULT 0
         mcp_schedule: SCHEDULE_TIME varchar(10) NOT NULL DEFAULT ''
         mcp_schedule: SCHEDULE_DAYS varchar(255) NOT NULL DEFAULT ''
         mcp_schedule: ACTION_TYPE varchar(50) NOT NULL DEFAULT ''
         mcp_schedule: ACTION_TARGET varchar(255) NOT NULL DEFAULT ''
         mcp_schedule: ACTION_STATE varchar(100) NOT NULL DEFAULT ''
         mcp_schedule: ACTION_DATA text
         mcp_schedule: CREATED datetime NOT NULL DEFAULT CURRENT_TIMESTAMP
         mcp_schedule: UPDATED timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
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
