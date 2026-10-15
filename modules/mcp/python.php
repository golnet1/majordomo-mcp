<?php

$err = false;
$file_err = '';
$phyton_info = '';
$has_problems = false; // были ли найдены несоответствия (для кнопки автоисправления)
$is_windows = strtoupper(substr(PHP_OS, 0, 3)) === 'WIN';

// Проверка существования конкретного файла в виртуальном окружении
$python_executable = $this->python_bin;  // это файл python3 в виртуальном окружении
if (file_exists($python_executable) && is_file($python_executable)) {
    DebMes("Файл Python существует: " . $python_executable, $this->name);
} else {
    DebMes("Файл Python НЕ существует: " . $python_executable, $this->name);
	$file_err = "Файл Python НЕ существует: " . $python_executable . "<br>";
	$err = true;
	$has_problems = true;
}

// Проверка существования конкретного файла в виртуальном окружении
$pip_executable = $this->pip_bin;  // это файл pip3 в виртуальном окружении
if (file_exists($pip_executable) && is_file($pip_executable)) {
    DebMes("Файл Pip существует: " . $pip_executable, $this->name);
} else {
    DebMes("Файл Pip НЕ существует: " . $pip_executable, $this->name);
	$file_err .= "Файл Pip НЕ существует: " . $pip_executable;
	$err = true;
	$has_problems = true;
}

if ($err) {
	$phyton_info = "<div class='form-group'>
    <label class='control-label'>
    </label>
    <div class='col-lg-8'>
        <div class='alert alert-warning' style='margin-top: 10px; margin-bottom: 0px'>
            Предупреждение: $file_err
        </div>
    </div>
</div>";
}

// Проверка существования директории
if (is_dir($this->venv_path)) {
    DebMes("Директория виртуального окружения существует: " . $this->venv_path, $this->name);
} else {
    DebMes("Директория виртуального окружения НЕ существует: " . $this->venv_path, $this->name);
	$phyton_info = "<div class='form-group'>
    <label class='control-label'>
    </label>
    <div class='col-lg-8'>
        <div class='alert alert-warning' style='margin-top: 10px; margin-bottom: 0px'>
            Предупреждение: Виртуальное окружение отсутствует.
        </div>
    </div>
</div>";
	$err = true;
	$has_problems = true;
}

// Проверяем версию Python
if (!$err) {
    $pythonVersionOutput = shell_exec(escapeshellarg($this->python_bin).' --version 2>&1');
    preg_match('/(\d+\.\d+\.\d+)/', $pythonVersionOutput, $matches);
    $pythonVersion = isset($matches[1]) ? $matches[1] : null;
    //$pythonVersion = "3.9";

    if ($pythonVersion) {
        //echo "Текущая версия Python: " . $pythonVersion . "\n";
    
        // Проверяем, что версия >= 3.10
        $versionArray = explode('.', $pythonVersion);
        $major = intval($versionArray[0]);
        $minor = intval($versionArray[1]);

        if ($major < 3 || ($major == 3 && $minor < 10)) {
		
            $phyton_info = "<div class='form-group'>
                <label class='control-label'>
                </label>
                <div class='col-lg-8'>
                    <div class='alert alert-warning' style='margin-top: 10px; margin-bottom: 0px'>
                        Предупреждение: Текущая Версия Python <font color='red'>$pythonVersion</font>. Должна быть не ниже 3.10.
                    </div>
                </div>
            </div>";
            $has_problems = true;
            //echo "Предупреждение: Версия Python ниже 3.10\n";

            // Проверяем, установлен ли pyenv
            if (!$is_windows) {
                $pyenv = '/opt/pyenv/bin/pyenv';
                $pyenvCheck = shell_exec('which ' . escapeshellarg($pyenv));
                if ($pyenvCheck) {
                    // Автообновление Python через pyenv здесь не выполняется:
                    // команда установки не формируется и ранее могла вызвать runtime warning.
                    DebMes("Pyenv найден. Автообновление Python отключено, выполните обновление вручную при необходимости.", $this->name);
                }
            }
        } else {
            $phyton_info = "";
            //echo "Версия Python актуальна.\n";
        }
	
        // Проверяем установленные библиотеки из requirements.txt
        if (file_exists(dirname(__FILE__) . '/lib/requirements.txt')) {
            $requirements = file_get_contents(dirname(__FILE__) . '/lib/requirements.txt');
            $req_lines = explode("\n", $requirements);
            $missing_packages = [];
		
            // Получаем список установленных пакетов
            if ($is_windows) {
                $installed_packages_output = shell_exec('"' . $this->pip_bin . '" list --format=freeze 2>NUL');
            } else {
                $installed_packages_output = shell_exec(escapeshellarg($this->pip_bin) . ' list --format=freeze 2>/dev/null');
            }
            $installed_packages = [];
            if ($installed_packages_output) {
                $installed_list = explode("\n", $installed_packages_output);
                foreach ($installed_list as $item) {
                    if (strpos($item, '==') !== false) {
                        list($name, $version) = explode('==', $item);
                        $installed_packages[strtolower(trim($name))] = trim($version);
                    }
                }
            }
            // Рабочий pip в окружении всегда перечисляет хотя бы сам pip==..., поэтому пустой
            // список означает, что pip не запустился (нет прав на запуск или битый скрипт) —
            // это отдельная проблема окружения, а не «все пакеты не установлены».
            $pip_unusable = empty($installed_packages);
		
            // Без списка установленных пакетов сравнение бессмысленно — не считаем всё отсутствующим.
            $lines_to_check = $pip_unusable ? [] : $req_lines;
            foreach ($lines_to_check as $line) {
                $line = trim($line);
                if (!empty($line) && !str_starts_with($line, '#')) {
                    // Разбиваем строку на имя пакета и версию
                    if (strpos($line, '>=') !== false) {
                        list($package, $version_req) = explode('>=', $line);
                    } elseif (strpos($line, '==') !== false) {
                        list($package, $version_req) = explode('==', $line);
                    } else {
                        $package = $line;
                        $version_req = null;
                    }
				
                    $package = trim($package);
				
                    // Проверяем, установлен ли пакет
                    if (!isset($installed_packages[strtolower($package)])) {
                        $missing_packages[] = $line;
                    } else {
                        // Если требуется версия, проверяем совместимость
                        if ($version_req) {
                            $installed_version = $installed_packages[strtolower($package)];
                            $version_req = trim($version_req);
						
                            // Проверяем, удовлетворяет ли установленная версия требованиям
                            $compare_result = version_compare($installed_version, $version_req, '<');
                            if ($compare_result) {
                                $missing_packages[] = "$package (требуется >= $version_req, установлена $installed_version)";
                            }
                        }
                    }
                }
            }
		
            if ($pip_unusable) {
                // pip не дал список — не утверждаем, что пакеты отсутствуют.
                $has_problems = true;
                $phyton_info .= "<div class='form-group'>
                    <label class='control-label'>
                    </label>
                    <div class='col-lg-8'>
                        <div class='alert alert-warning' style='margin-top: 10px; margin-bottom: 0px'>
                            Предупреждение! Не удалось получить список установленных пакетов:<br>
                            pip не запускается (нет прав на запуск либо битый скрипт).<br>
                            <code>" . htmlspecialchars($this->pip_bin, ENT_QUOTES) . "</code>
                        </div>
                    </div>
                </div>";
            } elseif (!empty($missing_packages)) {
                $has_problems = true;
                $missing_list = implode('<br>', array_map(function($pkg) {
                    return "<span style='color: red;'>• $pkg</span>";
                }, $missing_packages));
			
                $phyton_info .= "<div class='form-group'>
                    <label class='control-label'>
                    </label>
                    <div class='col-lg-8'>
                        <div class='alert alert-warning' style='margin-top: 10px; margin-bottom: 0px'>
                            Предупреждение! Не установлены следующие библиотеки для python:<br>$missing_list
                        </div>
                    </div>
                </div>";
            }
        }
    } else {
        //    echo "Python не найден или не установлен.\n";
    }
}

$out['PHYTON_INFO'] = $phyton_info;

// ---------------------------------------------------------------------------
// Автоисправление окружения (версия Python + зависимости).
// Кнопка показывается только если проверка нашла несоответствия.
// ---------------------------------------------------------------------------
$fix_body = '';

// Результат предыдущего запуска автоисправления (передан из admin()).
if (isset($mcp_fix_result) && is_array($mcp_fix_result)) {
    $fix_ok = !empty($mcp_fix_result['ok']);
    $rows = '';
    foreach ($mcp_fix_result['steps'] as $step) {
        $icon = $step['ok']
            ? "<span style='color:#3c763d'>✓</span>"
            : "<span style='color:#a94442'>✗</span>";
        $rows .= "<tr><td style='padding-right:10px;white-space:nowrap'>" . $icon . " "
            . htmlspecialchars($step['label'], ENT_QUOTES) . ":</td><td>"
            . htmlspecialchars($step['text'], ENT_QUOTES) . "</td></tr>";
    }
    $alert = $fix_ok ? 'alert-success' : 'alert-danger';
    $title = $fix_ok ? 'Исправление выполнено.' : 'Не все проблемы удалось исправить.';
    $fix_body .= "<div class='alert $alert' style='margin-top:10px;margin-bottom:0'>"
        . "<b>$title</b><br><table style='margin:0'><tbody>$rows</tbody></table></div>";
}

if ($has_problems) {
    $fix_body .= "<form action='?' method='post' style='margin:0'>
            <input type='hidden' name='view_mode' value='fix_python'>
            <button type='submit' class='btn btn-warning' onclick=\"return confirm('Исправить окружение Python? Будет подобран подходящий интерпретатор, при необходимости пересоздастся виртуальное окружение и установятся зависимости. Может занять минуту.');\">
                <i class='glyphicon glyphicon-wrench'></i> Исправить версию Python и зависимости
            </button>
            <div class='help-block' style='margin-bottom:0'>Проверит интерпретатор Python, при необходимости пересоздаст виртуальное окружение и установит зависимости из requirements.txt. Может занять минуту.</div>
        </form>";
}

// Оформляем в ту же сетку, что и остальные поля настроек (form-group / control-label / col-lg-8),
// иначе кнопка и результат «висят» без выравнивания.
$out['FIX_PANEL'] = '';
if ($fix_body !== '') {
    $out['FIX_PANEL'] = '<div class="form-group">'
        . '<label class="control-label">Окружение Python'
        . ' <span class="support-hover" tabindex="1" data-title="Проверка и автоисправление версии Python и зависимостей модуля.">'
        . '<i class="glyphicon glyphicon-info-sign"></i></span>'
        . '</label>'
        . '<div class="col-lg-8">' . $fix_body . '</div>'
        . '</div>';
}