<?php

function discover_manifest_paths($dataRoot)
{
    $results = [];
    if (!is_dir($dataRoot)) {
        return $results;
    }

    $iterator = new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator($dataRoot, FilesystemIterator::SKIP_DOTS)
    );
    foreach ($iterator as $fileInfo) {
        if (!$fileInfo->isFile()) {
            continue;
        }
        if ($fileInfo->getFilename() !== 'manifest.json') {
            continue;
        }
        $runDir = dirname($fileInfo->getPathname());
        if (!file_exists($runDir . '/COMPLETE')) {
            continue;
        }
        $results[] = $fileInfo->getPathname();
    }
    sort($results);
    return $results;
}

function resolve_manifest_output_path($manifestPath, $value)
{
    if (!is_string($value) || trim($value) === '') {
        return null;
    }

    $candidate = trim($value);

    // Backward compatibility for older manifests that stored absolute paths.
    if ($candidate[0] === '/') {
        return $candidate;
    }

    $manifestDir = dirname($manifestPath);
    return $manifestDir . '/' . ltrim($candidate, '/');
}

function import_manifest(array $config, $manifestPath)
{
    $pdo = db_conn($config);
    $raw = file_get_contents($manifestPath);
    if ($raw === false) {
        return ['ok' => false, 'reason' => 'unable_to_read_manifest'];
    }
    $payload = json_decode($raw, true);
    if (!is_array($payload)) {
        return ['ok' => false, 'reason' => 'invalid_json'];
    }

    $runId = isset($payload['run_id']) ? (string) $payload['run_id'] : '';
    $pulsar = isset($payload['pulsar']) ? (string) $payload['pulsar'] : '';
    if ($runId === '' || $pulsar === '') {
        return ['ok' => false, 'reason' => 'missing_run_id_or_pulsar'];
    }

    $runGeneratedUtc = isset($payload['created_utc']) ? (string) $payload['created_utc'] : null;
    $outputs = isset($payload['outputs']) && is_array($payload['outputs']) ? $payload['outputs'] : [];
    $summary = isset($payload['summary']) && is_array($payload['summary']) ? $payload['summary'] : [];
    $diagnosticsCsvPath = resolve_manifest_output_path($manifestPath, $outputs['diagnostics_csv'] ?? null);
    $outputTimPath = resolve_manifest_output_path($manifestPath, $outputs['output_tim'] ?? null);
    $diagnosticPlotPath = resolve_manifest_output_path($manifestPath, $outputs['diagnostic_plot'] ?? null);

    $stmt = $pdo->prepare(
        'INSERT OR IGNORE INTO runs (
            run_id, pulsar, run_generated_utc, imported_at_utc, manifest_path,
            diagnostics_csv_path, output_tim_path, diagnostic_plot_path,
            trusted_observations, new_observations, best_particle_log_weight
        ) VALUES (
            :run_id, :pulsar, :run_generated_utc, :imported_at_utc, :manifest_path,
            :diagnostics_csv_path, :output_tim_path, :diagnostic_plot_path,
            :trusted_observations, :new_observations, :best_particle_log_weight
        )'
    );

    $stmt->execute([
        ':run_id' => $runId,
        ':pulsar' => $pulsar,
        ':run_generated_utc' => $runGeneratedUtc,
        ':imported_at_utc' => db_now_utc(),
        ':manifest_path' => realpath($manifestPath) ?: $manifestPath,
        ':diagnostics_csv_path' => $diagnosticsCsvPath,
        ':output_tim_path' => $outputTimPath,
        ':diagnostic_plot_path' => $diagnosticPlotPath,
        ':trusted_observations' => isset($summary['trusted_observations']) ? (int) $summary['trusted_observations'] : null,
        ':new_observations' => isset($summary['new_observations']) ? (int) $summary['new_observations'] : null,
        ':best_particle_log_weight' => isset($summary['best_particle_log_weight']) ? (float) $summary['best_particle_log_weight'] : null,
    ]);

    return ['ok' => true, 'inserted' => $stmt->rowCount() > 0, 'run_id' => $runId, 'pulsar' => $pulsar];
}

function import_all_runs(array $config)
{
    $results = ['inserted' => 0, 'skipped' => 0, 'failed' => 0, 'details' => []];
    foreach (discover_manifest_paths($config['data_root']) as $manifestPath) {
        $result = import_manifest($config, $manifestPath);
        $results['details'][] = ['manifest' => $manifestPath, 'result' => $result];
        if (!$result['ok']) {
            $results['failed']++;
            continue;
        }
        if (!empty($result['inserted'])) {
            $results['inserted']++;
        } else {
            $results['skipped']++;
        }
    }
    return $results;
}

function get_pulsar_rule(array $config, $pulsar)
{
    $pdo = db_conn($config);
    $stmt = $pdo->prepare('SELECT * FROM pulsar_rules WHERE pulsar = :pulsar');
    $stmt->execute([':pulsar' => $pulsar]);
    return $stmt->fetch() ?: null;
}

function decide_run(array $config, $runId, $action, $actor, $postponeUntil, $note)
{
    $pdo = db_conn($config);
    $stmt = $pdo->prepare('SELECT * FROM runs WHERE run_id = :run_id');
    $stmt->execute([':run_id' => $runId]);
    $run = $stmt->fetch();
    if (!$run) {
        throw new RuntimeException('Run not found.');
    }

    $valid = ['accept', 'postpone', 'manual'];
    if (!in_array($action, $valid, true)) {
        throw new RuntimeException('Unsupported action.');
    }

    $status = $action === 'accept' ? 'accepted' : ($action === 'postpone' ? 'postponed' : 'manual');
    $decisionAt = db_now_utc();
    $noteValue = trim((string) $note);
    if ($noteValue === '') {
        $noteValue = null;
    }

    $pdo->beginTransaction();
    try {
        $updateRun = $pdo->prepare(
            'UPDATE runs
             SET status = :status,
                 decision_at_utc = :decision_at_utc,
                 decision_by = :decision_by,
                 postpone_until_utc = :postpone_until_utc,
                 decision_note = :decision_note
             WHERE run_id = :run_id'
        );
        $updateRun->execute([
            ':status' => $status,
            ':decision_at_utc' => $decisionAt,
            ':decision_by' => $actor,
            ':postpone_until_utc' => $action === 'postpone' ? $postponeUntil : null,
            ':decision_note' => $noteValue,
            ':run_id' => $runId,
        ]);

        if ($action === 'postpone') {
            if (!is_string($postponeUntil) || trim($postponeUntil) === '') {
                throw new RuntimeException('Postpone requires a postpone-until date.');
            }
            $ruleStmt = $pdo->prepare(
                'INSERT INTO pulsar_rules (pulsar, postpone_until_utc, source_run_id, updated_at_utc, updated_by)
                 VALUES (:pulsar, :postpone_until_utc, :source_run_id, :updated_at_utc, :updated_by)
                 ON CONFLICT(pulsar) DO UPDATE SET
                    postpone_until_utc = excluded.postpone_until_utc,
                    source_run_id = excluded.source_run_id,
                    updated_at_utc = excluded.updated_at_utc,
                    updated_by = excluded.updated_by'
            );
            $ruleStmt->execute([
                ':pulsar' => $run['pulsar'],
                ':postpone_until_utc' => $postponeUntil,
                ':source_run_id' => $runId,
                ':updated_at_utc' => $decisionAt,
                ':updated_by' => $actor,
            ]);
        }

        $pdo->commit();
    } catch (Throwable $error) {
        $pdo->rollBack();
        throw $error;
    }
}

function find_run(array $config, $runId)
{
    $pdo = db_conn($config);
    $stmt = $pdo->prepare('SELECT * FROM runs WHERE run_id = :run_id');
    $stmt->execute([':run_id' => $runId]);
    return $stmt->fetch() ?: null;
}

function list_runs_with_rules(array $config)
{
    $pdo = db_conn($config);
    $sql = 'SELECT r.*, pr.postpone_until_utc AS pulsar_postpone_until_utc
            FROM runs r
            LEFT JOIN pulsar_rules pr ON pr.pulsar = r.pulsar
            ORDER BY COALESCE(r.run_generated_utc, r.imported_at_utc) DESC';
    return $pdo->query($sql)->fetchAll();
}

function run_is_eligible_for_review($run)
{
    if (!is_array($run)) {
        return false;
    }
    if ((string) $run['status'] !== 'pending') {
        return false;
    }
    $ruleDate = isset($run['pulsar_postpone_until_utc']) ? trim((string) $run['pulsar_postpone_until_utc']) : '';
    if ($ruleDate === '') {
        return true;
    }
    $runDate = isset($run['run_generated_utc']) ? trim((string) $run['run_generated_utc']) : '';
    return $runDate !== '' && strcmp($runDate, $ruleDate) > 0;
}
