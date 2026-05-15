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
$validActions = ['clear_postponed', 'list_postponed', 'is_postponed'];
if (!in_array($action, $validActions, true)) {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'unsupported_action']);
    exit;
}

$pulsar = isset($payload['pulsar']) ? trim((string) $payload['pulsar']) : '';
$actor = isset($payload['cleared_by']) && trim((string) $payload['cleared_by']) !== ''
    ? trim((string) $payload['cleared_by'])
    : (getenv('USER') ?: 'api');
$note = isset($payload['note']) ? (string) $payload['note'] : '';

try {
    if ($action === 'clear_postponed') {
        if ($pulsar === '') {
            http_response_code(400);
            header('Content-Type: application/json');
            echo json_encode(['error' => 'missing_pulsar']);
            exit;
        }

        $result = clear_postponed_state_for_pulsar($APP_CONFIG, $pulsar, $actor, $note);
        header('Content-Type: application/json');
        echo json_encode([
            'ok' => true,
            'action' => $action,
            'pulsar' => $result['pulsar'],
            'postponed_runs_marked_outdated' => (int) $result['outdated_runs'],
            'pulsar_rules_deleted' => (int) $result['deleted_rules'],
            'decision_at_utc' => $result['decision_at_utc'],
            'decision_by' => $result['decision_by'],
            'decision_note' => $result['decision_note'],
            'postponed_pulsars' => list_active_postponed_pulsars($APP_CONFIG),
        ], JSON_PRETTY_PRINT);
        exit;
    }

    if ($action === 'list_postponed') {
        header('Content-Type: application/json');
        echo json_encode([
            'ok' => true,
            'action' => $action,
            'postponed_pulsars' => list_active_postponed_pulsars($APP_CONFIG),
        ], JSON_PRETTY_PRINT);
        exit;
    }

    if ($pulsar === '') {
        http_response_code(400);
        header('Content-Type: application/json');
        echo json_encode(['error' => 'missing_pulsar']);
        exit;
    }

    header('Content-Type: application/json');
    echo json_encode([
        'ok' => true,
        'action' => $action,
    ] + is_pulsar_postponed($APP_CONFIG, $pulsar), JSON_PRETTY_PRINT);
} catch (Throwable $error) {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['error' => $error->getMessage()]);
}
