<?php
function required_env(string $key): string {
    $value = getenv($key);

    if ($value === false || $value === '') {
        throw new RuntimeException("Missing required environment variable: {$key}");
    }

    return $value;
}

function optional_env(string $key, string $default): string {
    $value = getenv($key);
    if ($value === false || $value === '') {
        return $default;
    }
    return $value;
}

$defaultDataBase = dirname(__DIR__) . '/data';
$dataBase = optional_env('TIMING_DATA_BASE', $defaultDataBase);

return [
    'app_name' => 'Pulse Number Review',
    'timezone' => 'UTC',
    'data_root' => optional_env('TIMING_DATA_ROOT', $dataBase . '/runs'),
    'sqlite_path' => optional_env('TIMING_SQLITE_PATH', $dataBase . '/db/review.sqlite'),
    'shared_password' => required_env('TIMING_WEB_PASSWORD'),
    'api_key' => required_env('TIMING_API_KEY'),
];
