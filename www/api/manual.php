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

$action = isset($payload['action']) ? trim((string) $payload['action']) : '';
if ($action !== 'clear_manual') {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'unsupported_action']);
    exit;
}

$runId = isset($payload['run_id']) ? trim((string) $payload['run_id']) : '';
$pulsar = isset($payload['pulsar']) ? trim((string) $payload['pulsar']) : '';
if ($runId === '' && $pulsar === '') {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'missing_run_id_or_pulsar']);
    exit;
}

$actor = isset($payload['cleared_by']) && trim((string) $payload['cleared_by']) !== ''
    ? trim((string) $payload['cleared_by'])
    : (getenv('USER') ?: 'api');
$note = isset($payload['note']) ? (string) $payload['note'] : '';

try {
    $run = clear_manual_run($APP_CONFIG, $actor, $runId !== '' ? $runId : null, $pulsar !== '' ? $pulsar : null, $note);
    header('Content-Type: application/json');
    echo json_encode([
        'ok' => true,
        'run_id' => $run['run_id'] ?? null,
        'pulsar' => $run['pulsar'] ?? null,
        'status' => $run['status'] ?? 'manual_cleared',
        'decision_at_utc' => $run['decision_at_utc'] ?? null,
        'decision_by' => $run['decision_by'] ?? $actor,
        'decision_note' => $run['decision_note'] ?? null,
    ], JSON_PRETTY_PRINT);
} catch (Throwable $error) {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['error' => $error->getMessage()]);
}