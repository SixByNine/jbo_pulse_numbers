<?php
$APP_CONFIG = require __DIR__ . '/../config.php';

date_default_timezone_set($APP_CONFIG['timezone']);
if (session_status() !== PHP_SESSION_ACTIVE) {
    session_start();
}

require_once __DIR__ . '/db.php';
require_once __DIR__ . '/auth.php';
require_once __DIR__ . '/review.php';

init_db($APP_CONFIG);
