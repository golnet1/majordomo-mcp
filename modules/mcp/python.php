<?php

$err = false;

// Проверка существования конкретного файла в виртуальном окружении
$python_executable = $this->python_bin;  // это файл python3 в виртуальном окружении
if (file_exists($python_executable) && is_file($python_executable)) {
    DebMes("Файл Python существует: " . $python_executable, $this->name);
} else {
    DebMes("Файл Python НЕ существует: " . $python_executable, $this->name);
	$file_err = "Файл Python НЕ существует: " . $python_executable . "<br>";
	$err = true;
}

// Проверка существования конкретного файла в виртуальном окружении
$pip_executable = $this->pip_bin;  // это файл pip3 в виртуальном окружении
if (file_exists($pip_executable) && is_file($pip_executable)) {
    DebMes("Файл Pip существует: " . $pip_executable, $this->name);
} else {
    DebMes("Файл Pip НЕ существует: " . $pip_executable, $this->name);
	$file_err .= "Файл Pip НЕ существует: " . $pip_executable;
	$err = true;
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
}

// Проверяем версию Python
if (!$err) {
    $pythonVersionOutput = shell_exec($this->python_bin.' --version 2>&1');
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
            //echo "Предупреждение: Версия Python ниже 3.10\n";

            // Проверяем, установлен ли pyenv
            $pyenv = '/opt/pyenv/bin/pyenv';
            $pyenvCheck = shell_exec('which '.$pyenv);
            if ($pyenvCheck) {
                //echo "Pyenv установлен, пытаемся обновить Python...\n";
            
                // Устанавливаем Python 3.10 под пользователем www-data
                //$installCmd = $pyenv.' install 3.10.0; ';
                //$installCmd .= $pyenv' global 3.10.0;"';
            
                $output = shell_exec($installCmd . ' 2>&1');
                //echo "Вывод установки: " . $output . "\n";
            } else {
                //echo "Pyenv не установлен. Установите pyenv для возможности обновления Python.\n";
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
            $installed_packages_output = shell_exec($this->pip_bin.' list --format=freeze 2>/dev/null');
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
		
            foreach ($req_lines as $line) {
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
		
            if (!empty($missing_packages)) {
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