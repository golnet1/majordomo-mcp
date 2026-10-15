<?php
chdir(dirname(__FILE__) . '/../');

include_once("./config.php");
include_once("./lib/loader.php");
include_once("./lib/threads.php");

$latest_check = 0;
$checkEvery = 5; // poll every 5 seconds

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

echo date("H:i:s") . " running " . basename(__FILE__) . PHP_EOL;


while (1) {
    setGlobal((str_replace('.php', '', basename(__FILE__))) . 'Run', time(), 1);
    $control_status = getGlobal((str_replace('.php', '', basename(__FILE__))) . 'Exit');
	if ($control_status) {
        $MCP_module->stopMcpPipeProcess();
		setGlobal((str_replace('.php', '', basename(__FILE__))) . 'Exit', false);
	}
    // --- Проверка и запуск/перезапуск Python-процесса ---
    // Проверяем, нужно ли завершить цикл
    if (file_exists('./reboot') || IsSet($_GET['onetime'])) {
        // Завершаем Python-процесс при выходе из основного цикла
        $current_pid = $MCP_module->ensureSingleMcpPipeProcess();

        if ($current_pid !== null) {
            echo date('Y-m-d H:i:s') . " Attempting to terminate Python mcp_pipe.py process (PID: $current_pid) before exit.\n";

            // Проверим, запущен ли процесс с этим PID
            if ($MCP_module->isProcessRunning($current_pid)) {
                $kill_return = $MCP_module->terminateProcess($current_pid) ? 0 : 1;

                if ($kill_return === 0) {
                    echo date('Y-m-d H:i:s') . " Sent SIGTERM to Python mcp_pipe.py (PID: $current_pid).\n";
                    sleep(1); // Ждем короткое время

                    // Проверим, жив ли процесс снова
                    if ($MCP_module->isProcessRunning($current_pid)) {
                        echo date('Y-m-d H:i:s') . " Process (PID: $current_pid) still alive after termination request, forcing stop.\n";
                        $kill_return2 = $MCP_module->terminateProcess($current_pid) ? 0 : 1;
                        if ($kill_return2 === 0) {
                             echo date('Y-m-d H:i:s') . " Forced stop sent to Python mcp_pipe.py (PID: $current_pid).\n";
                        } else {
                             echo date('Y-m-d H:i:s') . " Failed to force-stop Python mcp_pipe.py (PID: $current_pid).\n";
                        }
                        sleep(1); // Подождем, чтобы принудительная остановка сработала
                    } else {
                        echo date('Y-m-d H:i:s') . " Python mcp_pipe.py (PID: $current_pid) terminated gracefully after SIGTERM.\n";
                    }
                } else {
                    echo date('Y-m-d H:i:s') . " Failed to send SIGTERM to Python mcp_pipe.py (PID: $current_pid).\n";
                }
            } else {
                echo date('Y-m-d H:i:s') . " Process with PID $current_pid does not seem to exist anymore.\n";
            }
        } else {
            echo date('Y-m-d H:i:s') . " No Python mcp_pipe.py process found to terminate.\n";
        }
        $db->Disconnect();
        exit;
    } else {

        $current_pid = $MCP_module->ensureSingleMcpPipeProcess();
        $isPythonRunning = ($current_pid !== null);
//echo $current_pid[$pids - 1]."\n";
        if (!$isPythonRunning) {
            // Процесс умер или не запущен
            $mcpPipeStarted = false;
            echo date('Y-m-d H:i:s') . " Python mcp_pipe.py process not found. Marking for restart.\n";
        } else {
            $mcpPipeStarted = $MCP_module->isProcessRunning($current_pid);
        }

        // Попробовать запустить процесс, если он не запущен и прошло достаточно времени с последней неудачной попытки
        if (!$mcpPipeStarted && (time() - $mcpPipeLastRestartAttempt) >= $mcpPipeFailedRestartDelay) {
            echo date('Y-m-d H:i:s') . " Attempting to start Python mcp_pipe.py in background.\n";

            $MCP_module->getConfig();
            $mcpEndpoint = isset($MCP_module->config['MCP_ENDPOINT']) ? trim($MCP_module->config['MCP_ENDPOINT']) : '';
            $started = $MCP_module->startMcpPipeProcess($mcpEndpoint);
            if ($started) {
                // Ждем короткое время, чтобы mcp_pipe.py мог начать работу
                sleep($mcpPipeCheckAfterStart);

                $new_pid = $MCP_module->ensureSingleMcpPipeProcess();

                if ($new_pid !== null) {
                    // Проверим, запущен ли процесс с этим PID *сейчас*
                    if ($MCP_module->isProcessRunning($new_pid)) {
                        echo date('Y-m-d H:i:s') . " Python mcp_pipe.py started successfully in background (PID: $new_pid).\n";
                        $mcpPipeStarted = true; // Успешно запущен и жив
                        $mcpPipeLastRestartAttempt = time(); // Обновим время последней попытки (успешной)
                    } else {
                        // Процесс был найден по имени, но не запущен согласно ps
                        echo date('Y-m-d H:i:s') . " Python mcp_pipe.py (PID: $new_pid) is not running after startup. Check logs.\n";
                        $mcpPipeStarted = false; // Неудачная попытка
                        $mcpPipeLastRestartAttempt = time(); // Обновим время последней попытки (неудачной)
                    }
                } else {
                    // PID не был найден после запуска
                    echo date('Y-m-d H:i:s') . " Failed to find Python mcp_pipe.py PID after starting. It might have failed to start.\n";
                    $mcpPipeStarted = false; // Неудачная попытка
                    $mcpPipeLastRestartAttempt = time(); // Обновим время последней попытки (неудачной)
                }
            } else {
                echo date('Y-m-d H:i:s') . " Failed to execute command to start Python mcp_pipe.py.\n";
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