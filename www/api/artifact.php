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

$runId = isset($_GET['run_id']) ? (string) $_GET['run_id'] : '';
$type = isset($_GET['type']) ? (string) $_GET['type'] : 'tim';

$run = find_run($APP_CONFIG, $runId);
if (!$run) {
    http_response_code(404);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'run_not_found']);
    exit;
}

$map = [
    'tim' => ['column' => 'output_tim_path', 'mime' => 'text/plain'],
    'csv' => ['column' => 'diagnostics_csv_path', 'mime' => 'text/csv'],
    'plot' => ['column' => 'diagnostic_plot_path', 'mime' => 'image/png'],
    'manifest' => ['column' => 'manifest_path', 'mime' => 'application/json'],
];

if (!isset($map[$type])) {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'unsupported_type']);
    exit;
}

$path = isset($run[$map[$type]['column']]) ? (string) $run[$map[$type]['column']] : '';
if ($path === '' || !file_exists($path)) {
    http_response_code(404);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'artifact_not_found']);
    exit;
}

header('Content-Type: ' . $map[$type]['mime']);
header('Content-Disposition: inline; filename="' . basename($path) . '"');
readfile($path);
