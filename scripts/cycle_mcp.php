<?php
chdir(dirname(__FILE__) . '/../');

include_once("./config.php");
include_once("./lib/loader.php");
include_once("./lib/threads.php");

$latest_check = 0;
$checkEvery = 5; // poll every 5 seconds

$pythonScriptPath = './modules/mcp/lib/mcp_pipe.py'; // путь к mcp_pipe.py
$mcpPipeStarted = false; // Флаг, показывающий, что процесс был запущен (успешно или нет)
$mcpPipeFailedRestartDelay = 10; // Задержка в секундах перед повторной попыткой запуска после сбоя
$mcpPipeLastRestartAttempt = 0; // Время последней попытки перезапуска
$mcpPipeCheckAfterStart = 2; // Секунды ожидания после запуска, чтобы проверить, жив ли процесс

set_time_limit(0);

// connecting to database
$db = new mysql(DB_HOST, '', DB_USER, DB_PASSWORD, DB_NAME);
include_once("./load_settings.php");
include_once(DIR_MODULES . "control_modules/control_modules.class.php");
$ctl = new control_modules();
include_once(DIR_MODULES . 'mcp/mcp.class.php');

$MCP_module = new mcp();
$MCP_module->getConfig();

$pythonExecutable = $MCP_module->python_bin; // путь к Python

echo date("H:i:s") . " running " . basename(__FILE__) . PHP_EOL;


while (1) {
    setGlobal((str_replace('.php', '', basename(__FILE__))) . 'Run', time(), 1);
    $control_status = getGlobal((str_replace('.php', '', basename(__FILE__))) . 'Exit');
	if ($control_status) {
        $current_pid = $MCP_module->getPidByName($MCP_module->mcpPipeProcessName);
        if ($current_pid !== null) {
		    if ($MCP_module->isProcessRunning($current_pid)) {
                // Отправляем SIGTERM
                $kill_term_cmd = "kill -TERM $current_pid 2>/dev/null";
                exec($kill_term_cmd, $kill_output, $kill_return);
            }
		}
		setGlobal((str_replace('.php', '', basename(__FILE__))) . 'Exit', false);
	}
    // --- Проверка и запуск/перезапуск Python-процесса ---
    // Проверяем, нужно ли завершить цикл
    if (file_exists('./reboot') || IsSet($_GET['onetime'])) {
        // Завершаем Python-процесс при выходе из основного цикла, используя pgrep для поиска PID
        $current_pid = $MCP_module->getPidByName($MCP_module->mcpPipeProcessName);

        if ($current_pid !== null) {
            echo date('Y-m-d H:i:s') . " Attempting to terminate Python mcp_pipe.py process (PID: $current_pid) found by pgrep before exit.\n";

            // Проверим, запущен ли процесс с этим PID
            if ($MCP_module->isProcessRunning($current_pid)) {
                // Отправляем SIGTERM
                $kill_term_cmd = "kill -TERM $current_pid 2>/dev/null";
                exec($kill_term_cmd, $kill_output, $kill_return);

                if ($kill_return === 0) {
                    echo date('Y-m-d H:i:s') . " Sent SIGTERM to Python mcp_pipe.py (PID: $current_pid).\n";
                    sleep(1); // Ждем короткое время

                    // Проверим, жив ли процесс снова
                    if ($MCP_module->isProcessRunning($current_pid)) {
                        echo date('Y-m-d H:i:s') . " Process (PID: $current_pid) still alive after SIGTERM, sending SIGKILL.\n";
                        $kill_kill_cmd = "kill -KILL $current_pid 2>/dev/null";
                        exec($kill_kill_cmd, $kill_output2, $kill_return2);
                        if ($kill_return2 === 0) {
                             echo date('Y-m-d H:i:s') . " Sent SIGKILL to Python mcp_pipe.py (PID: $current_pid).\n";
                        } else {
                             echo date('Y-m-d H:i:s') . " Failed to send SIGKILL to Python mcp_pipe.py (PID: $current_pid).\n";
                        }
                        sleep(1); // Подождем, чтобы SIGKILL сработал
                    } else {
                        echo date('Y-m-d H:i:s') . " Python mcp_pipe.py (PID: $current_pid) terminated gracefully after SIGTERM.\n";
                    }
                } else {
                    echo date('Y-m-d H:i:s') . " Failed to send SIGTERM to Python mcp_pipe.py (PID: $current_pid).\n";
                }
            } else {
                echo date('Y-m-d H:i:s') . " Process with PID $current_pid from pgrep does not seem to exist anymore.\n";
            }
        } else {
            echo date('Y-m-d H:i:s') . " No Python mcp_pipe.py process found by pgrep to terminate.\n";
        }
        $db->Disconnect();
        exit;
    } else {

        // Проверяем состояние процесса через pgrep
        $current_pid = $MCP_module->getPidByName($MCP_module->mcpPipeProcessName, 1);
		if ($current_pid !== null) $pids = count($current_pid);
		else $pids = 0;
        $isPythonRunning = ($current_pid !== null);
//echo $current_pid[$pids - 1]."\n";
        if (!$isPythonRunning) {
            // Процесс умер или не запущен
            $mcpPipeStarted = false;
            if ($current_pid !== null) {
                // Это маловероятно, но если PID был найден, но процесс не запущен согласно isProcessRunning
                echo date('Y-m-d H:i:s') . " Python mcp_pipe.py process (PID: $current_pid[0] found by pgrep) seems dead. Marking for restart.\n";
            } else {
                echo date('Y-m-d H:i:s') . " Python mcp_pipe.py process not found by pgrep. Marking for restart.\n";
            }
        } else {
            // Процесс найден, проверим дополнительно через ps
 //           if (!$MCP_module->isProcessRunning($current_pid[$pids - 1])) {
 //               $mcpPipeStarted = false;
 //               echo date('Y-m-d H:i:s') . " Python mcp_pipe.py process (PID: $current_pid found by pgrep) not running according to ps. Marking for restart.\n";
 //           }
            // Если PID найден и процесс запущен и он один, ничего не делаем, всё ок.
			if ($pids > 1) {
				for ($i = 0; $i < $pids - 1; $i++) {
					 $kill_term_cmd = "kill -TERM $current_pid[$i] 2>/dev/null";
					 exec($kill_term_cmd, $kill_output, $kill_return);
				}
			}
        }

        // Попробовать запустить процесс, если он не запущен и прошло достаточно времени с последней неудачной попытки
        if (!$mcpPipeStarted && (time() - $mcpPipeLastRestartAttempt) >= $mcpPipeFailedRestartDelay) {
            // Подготовим команду запуска - используем nohup и & для запуска в фоне
            // и НЕ будем пытаться записать PID через echo $!, будем использовать pgrep
            $workingDir = dirname($pythonScriptPath); // /var/www/html/modules/mcp/lib
            // Добавим уникальный префикс к имени процесса, чтобы pgrep мог его точно найти
            // или используем уже уникальное имя 'python3.*mcp_pipe\.py'
            $cmd = "(cd " . escapeshellarg($workingDir) . " && MCP_ENDPOINT=\"\$(cat .env 2>/dev/null | grep MCP_ENDPOINT | cut -d '=' -f2- 2>/dev/null)\" nohup $pythonExecutable " . escapeshellarg(basename($pythonScriptPath)) . " > /tmp/mcp_pipe_out.log 2>&1 &);";
            // Эта команда:
            // 1. Меняет директорию на $workingDir
            // 2. Устанавливает MCP_ENDPOINT из .env
            // 3. Запускает mcp_pipe.py в фоне с nohup
            // PID не записывается в файл, мы будем искать его через pgrep
            echo date('Y-m-d H:i:s') . " Attempting to start Python mcp_pipe.py in background: $cmd\n";

            // Запускаем процесс в фоне через exec
            exec($cmd, $output, $return_var);
            if ($return_var === 0) {
                // Ждем короткое время, чтобы mcp_pipe.py мог начать работу
                sleep($mcpPipeCheckAfterStart);

                $new_pid = $MCP_module->getPidByName($MCP_module->mcpPipeProcessName);

                if ($new_pid !== null) {
                    // Проверим, запущен ли процесс с этим PID *сейчас*
                    if ($MCP_module->isProcessRunning($new_pid)) {
                        echo date('Y-m-d H:i:s') . " Python mcp_pipe.py started successfully in background (found PID: $new_pid via pgrep).\n";
                        $mcpPipeStarted = true; // Успешно запущен и жив
                        $mcpPipeLastRestartAttempt = time(); // Обновим время последней попытки (успешной)
                    } else {
                        // Процесс был найден по имени, но не запущен согласно ps
                        echo date('Y-m-d H:i:s') . " Python mcp_pipe.py (found PID: $new_pid via pgrep) not running according to ps after startup. Check logs.\n";
                        $mcpPipeStarted = false; // Неудачная попытка
                        $mcpPipeLastRestartAttempt = time(); // Обновим время последней попытки (неудачной)
                    }
                } else {
                    // PID не был найден через pgrep
                    echo date('Y-m-d H:i:s') . " Failed to find Python mcp_pipe.py PID via pgrep after starting. It might have failed to start.\n";
                    $mcpPipeStarted = false; // Неудачная попытка
                    $mcpPipeLastRestartAttempt = time(); // Обновим время последней попытки (неудачной)
                }
            } else {
                echo date('Y-m-d H:i:s') . " Failed to execute command to start Python mcp_pipe.py (exec returned $return_var).\n";
                $mcpPipeStarted = false; // Неудачная попытка
                $mcpPipeLastRestartAttempt = time(); // Обновим время последней попытки (неудачной)
            }
        }    
        // --- Конец проверки и запуска Python-процесса ---

        if ((time() - $latest_check) > $checkEvery) {
            $latest_check = time();
        //    echo date('Y-m-d H:i:s') . ' Polling devices...';
            $MCP_module->processCycle();
        }
    }
    sleep(1);
}

DebMes("Unexpected close of cycle: " . basename(__FILE__), "Xray");