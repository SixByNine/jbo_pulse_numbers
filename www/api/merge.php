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
if ($runId === '') {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'missing_run_id']);
    exit;
}

$mergedBy = isset($payload['merged_by']) && trim((string) $payload['merged_by']) !== ''
    ? trim((string) $payload['merged_by'])
    : 'godrevy';
$mergeNote = isset($payload['merge_note']) ? (string) $payload['merge_note'] : '';

try {
    $run = mark_run_merged($APP_CONFIG, $runId, $mergedBy, $mergeNote);
    header('Content-Type: application/json');
    echo json_encode([
        'ok' => true,
        'run_id' => $runId,
        'status' => $run['status'] ?? 'merged',
        'merged_at_utc' => $run['merged_at_utc'] ?? null,
        'merged_by' => $run['merged_by'] ?? $mergedBy,
    ], JSON_PRETTY_PRINT);
} catch (Throwable $error) {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['error' => $error->getMessage()]);
}
