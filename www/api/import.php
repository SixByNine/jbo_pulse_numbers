<?php
require_once __DIR__ . '/../lib/bootstrap.php';

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'method_not_allowed']);
    exit;
}

$configuredKey = (string) $APP_CONFIG['api_key'];
if ($configuredKey !== '') {
    $provided = isset($_GET['key']) ? (string) $_GET['key'] : '';
    if (!hash_equals($configuredKey, $provided)) {
        http_response_code(403);
        header('Content-Type: application/json');
        echo json_encode(['error' => 'forbidden']);
        exit;
    }
}

$raw = file_get_contents('php://input');
$payload = json_decode($raw === false ? '' : $raw, true);
if (!is_array($payload)) {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'invalid_json']);
    exit;
}

$runId = isset($payload['run_id']) ? trim((string) $payload['run_id']) : '';
$pulsar = isset($payload['pulsar']) ? trim((string) $payload['pulsar']) : '';
if ($runId === '' || $pulsar === '') {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'missing_pulsar_or_run_id']);
    exit;
}

try {
    $result = import_single_run($APP_CONFIG, $pulsar, $runId);
    header('Content-Type: application/json');
    echo json_encode($result, JSON_PRETTY_PRINT);
} catch (Throwable $error) {
    $message = $error->getMessage();
    $status = in_array($message, ['run_not_found', 'manifest_not_found', 'complete_marker_not_found'], true)
        ? 404
        : 400;
    http_response_code($status);
    header('Content-Type: application/json');
    echo json_encode([
        'ok' => false,
        'error' => $message,
        'run_id' => $runId,
        'pulsar' => $pulsar,
    ]);
}