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
$stmt = $pdo->query(
    'SELECT pulsar, COUNT(*) AS accepted_count, GROUP_CONCAT(run_id, ",") AS run_ids
     FROM runs
     WHERE status = "accepted"
     GROUP BY pulsar
     ORDER BY pulsar ASC'
);
$rows = $stmt->fetchAll();

$pulsars = [];
foreach ($rows as $row) {
    $runIds = [];
    if (!empty($row['run_ids'])) {
        $runIds = array_values(array_filter(array_map('trim', explode(',', (string) $row['run_ids']))));
    }
    $pulsars[] = [
        'pulsar' => (string) $row['pulsar'],
        'accepted_count' => (int) $row['accepted_count'],
        'run_ids' => $runIds,
    ];
}

header('Content-Type: application/json');
echo json_encode([
    'generated_utc' => db_now_utc(),
    'pulsars' => $pulsars,
    'count' => count($pulsars),
], JSON_PRETTY_PRINT);
