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

$pulsarRules = $pdo->query('SELECT * FROM pulsar_rules ORDER BY pulsar')->fetchAll();

$accepted = array_values(array_filter($runs, function ($run) {
    return isset($run['status']) && (string) $run['status'] === 'accepted';
}));

$payload = [
    'generated_utc' => db_now_utc(),
    'filters' => [
        'pulsar' => $pulsarFilter !== '' ? $pulsarFilter : null,
        'status' => $statusFilter !== '' ? $statusFilter : null,
    ],
    'runs' => $runs,
    'accepted_runs' => $accepted,
    'pulsar_rules' => $pulsarRules,
];

header('Content-Type: application/json');
echo json_encode($payload, JSON_PRETTY_PRINT);
