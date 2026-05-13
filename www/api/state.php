<?php
require_once __DIR__ . '/../lib/bootstrap.php';

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

$pdo = db_conn($APP_CONFIG);
$runs = list_runs_with_rules($APP_CONFIG);
$pulsarFilter = isset($_GET['pulsar']) ? trim((string) $_GET['pulsar']) : '';
$statusFilter = isset($_GET['status']) ? trim((string) $_GET['status']) : '';
$manualOnly = isset($_GET['manual_only'])
    ? in_array(strtolower(trim((string) $_GET['manual_only'])), ['1', 'true', 'yes'], true)
    : false;

if ($pulsarFilter !== '') {
    $runs = array_values(array_filter($runs, function ($run) use ($pulsarFilter) {
        return isset($run['pulsar']) && (string) $run['pulsar'] === $pulsarFilter;
    }));
}
if ($statusFilter !== '') {
    $runs = array_values(array_filter($runs, function ($run) use ($statusFilter) {
        return isset($run['status']) && (string) $run['status'] === $statusFilter;
    }));
}

$manualRuns = array_values(array_filter($runs, function ($run) {
    return isset($run['status']) && (string) $run['status'] === 'manual';
}));

$errorRuns = array_values(array_filter($runs, function ($run) {
    return isset($run['status']) && (string) $run['status'] === 'error';
}));

if ($manualOnly) {
    $runs = $manualRuns;
}

$pulsarRules = $pdo->query('SELECT * FROM pulsar_rules ORDER BY pulsar')->fetchAll();

$accepted = array_values(array_filter($runs, function ($run) {
    return isset($run['status']) && (string) $run['status'] === 'accepted';
}));

$merged = array_values(array_filter($runs, function ($run) {
    return isset($run['status']) && (string) $run['status'] === 'merged';
}));

$manualPulsars = [];
foreach ($manualRuns as $run) {
    $pulsar = isset($run['pulsar']) ? (string) $run['pulsar'] : '';
    if ($pulsar === '' || isset($manualPulsars[$pulsar])) {
        continue;
    }
    $manualPulsars[$pulsar] = [
        'pulsar' => $pulsar,
        'run_id' => (string) ($run['run_id'] ?? ''),
        'decision_at_utc' => $run['decision_at_utc'] ?? null,
        'decision_by' => $run['decision_by'] ?? null,
        'decision_note' => $run['decision_note'] ?? null,
    ];
}

$payload = [
    'generated_utc' => db_now_utc(),
    'filters' => [
        'pulsar' => $pulsarFilter !== '' ? $pulsarFilter : null,
        'status' => $statusFilter !== '' ? $statusFilter : null,
        'manual_only' => $manualOnly,
    ],
    'runs' => $runs,
    'accepted_runs' => $accepted,
    'merged_runs' => $merged,
    'manual_runs' => $manualRuns,
    'manual_pulsars' => array_values($manualPulsars),
    'error_runs' => $errorRuns,
    'pulsar_rules' => $pulsarRules,
];

header('Content-Type: application/json');
echo json_encode($payload, JSON_PRETTY_PRINT);
