<?php
require_once __DIR__ . '/lib/bootstrap.php';
require_login();

$runId = isset($_GET['run_id']) ? (string) $_GET['run_id'] : '';
$type = isset($_GET['type']) ? (string) $_GET['type'] : '';
$run = find_run($APP_CONFIG, $runId);
if (!$run) {
    http_response_code(404);
    echo 'Run not found';
    exit;
}

$map = [
    'plot' => ['path' => $run['diagnostic_plot_path'], 'mime' => 'image/png'],
    'csv' => ['path' => $run['diagnostics_csv_path'], 'mime' => 'text/csv'],
    'tim' => ['path' => $run['output_tim_path'], 'mime' => 'text/plain'],
    'manifest' => ['path' => $run['manifest_path'], 'mime' => 'application/json'],
];

if (!isset($map[$type])) {
    http_response_code(400);
    echo 'Unknown artifact type';
    exit;
}

$path = $map[$type]['path'];
if (!is_string($path) || $path === '' || !file_exists($path)) {
    http_response_code(404);
    echo 'Artifact not found';
    exit;
}

header('Content-Type: ' . $map[$type]['mime']);
readfile($path);
