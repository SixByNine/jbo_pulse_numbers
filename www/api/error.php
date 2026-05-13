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

$pulsar = isset($payload['pulsar']) ? trim((string) $payload['pulsar']) : '';
$runId = isset($payload['run_id']) ? trim((string) $payload['run_id']) : '';
$message = isset($payload['message']) ? trim((string) $payload['message']) : '';

if ($pulsar === '' || $runId === '' || $message === '') {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'missing_pulsar_run_id_or_message']);
    exit;
}

try {
    $run = log_error_run($APP_CONFIG, $pulsar, $runId, $message);
    header('Content-Type: application/json');
    echo json_encode([
        'ok' => true,
        'run_id' => $run['run_id'] ?? $runId,
        'pulsar' => $run['pulsar'] ?? $pulsar,
        'status' => $run['status'] ?? 'error',
        'decision_at_utc' => $run['decision_at_utc'] ?? null,
        'decision_note' => $run['decision_note'] ?? $message,
    ], JSON_PRETTY_PRINT);
} catch (Throwable $error) {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['error' => $error->getMessage()]);
}
