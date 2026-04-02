<?php

function generateMacAddress() {
    $parts = [];
    for ($i = 0; $i < 6; $i++) {
        $parts[] = str_pad(dechex(rand(0, 255)), 2, '0', STR_PAD_LEFT);
    }
    return implode(':', $parts);
}
 
function uuidv4() {
    $data = random_bytes(16);
    $data[6] = chr(ord($data[6]) & 0x0f | 0x40); // version 4
    $data[8] = chr(ord($data[8]) & 0x3f | 0x80); // variant RFC 4122
    $hex = bin2hex($data);
    return sprintf('%08s-%04s-%04s-%04s-%12s',
        substr($hex, 0, 8),
        substr($hex, 8, 4),
        substr($hex, 12, 4),
        substr($hex, 16, 4),
        substr($hex, 20, 12)
    );
}

$url = 'https://api.tenclass.net/xiaozhi/ota/';
$mac = generateMacAddress();
$uuid = uuidv4();

$data = array(
    'application' => array(
        'version' => '2.0.1',
    	'mac_address'=> $mac,
    	"uuid"=>$uuid,
    	),
    	'board' => array(
    	'type' => 'smartfon'
    	)

	);

$options = [
    'http' => [
        'header' => "Activation-Version: 2\r\n" .
    				"Device-Id: $mac\r\n".
    				"Client-Id: $uuid\r\n".
     				"Content-type: application/json\r\n",
        'method' => 'POST',
        'content' => json_encode($data)
    ],
];

$context = stream_context_create($options);
$result = file_get_contents($url, false, $context);
$res = json_decode($result, true);
//dprint($res, false);

if ($res && isset($res['activation']['code'])) {

    // Передаём данные в шаблон
	$activation_code = $res['activation']['code'];
    $out['GENERATED_MAC'] = $mac;
    $out['GENERATED_CODE'] = $activation_code;
	// Активация
//    echo '---------';
//    echo 'Активация';
//    echo '---------';

    $url = 'https://api.tenclass.net/xiaozhi/ota/activate';

    $data = array(
        'algorithm' => 'hmac-sha256',
	);

    $options = [
        'http' => [
            'header' => "Activation-Version: 2\r\n" .
    				    "Device-Id: $mac\r\n".
    				    "Client-Id: $uuid\r\n".
     				    "Content-type: application/json\r\n",
            'method' => 'POST',
            'content' => json_encode($data)
        ],
    ];

    $context = stream_context_create($options);
    $result = file_get_contents($url, false, $context);
    $res = json_decode($result, true);
    //dprint($res, false);
} else {
    // Обработка ошибки
    $out['GENERATION_ERROR'] = "Не удалось получить код активации.";
    $out['GENERATED_MAC'] = '';
    $out['GENERATED_CODE'] = '';
    // Добавьте логирование ошибки, если нужно
    // DebMes("Ошибка генерации данных: " . print_r($res_register, true), $this->name);	
}
