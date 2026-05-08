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

function mark_older_pending_runs_outdated(PDO $pdo, $pulsar, $runId, $newRunSortUtc)
{
    $note = 'Outdated by newer imported run ' . (string) $runId;
    $stmt = $pdo->prepare(
        'UPDATE runs
         SET status = "outdated",
             decision_at_utc = :decision_at_utc,
             decision_by = :decision_by,
             postpone_until_utc = NULL,
             decision_note = :decision_note,
             merged_at_utc = NULL,
             merged_by = NULL,
             merge_note = NULL
         WHERE pulsar = :pulsar
           AND run_id <> :run_id
           AND status = "pending"
           AND COALESCE(run_generated_utc, imported_at_utc, "") < :new_sort_utc'
    );
    $stmt->execute([
        ':decision_at_utc' => db_now_utc(),
        ':decision_by' => 'system_import',
        ':decision_note' => $note,
        ':pulsar' => $pulsar,
        ':run_id' => $runId,
        ':new_sort_utc' => $newRunSortUtc,
    ]);

    return $stmt->rowCount();
}

function find_active_manual_run_for_pulsar(PDO $pdo, $pulsar)
{
    $stmt = $pdo->prepare(
        'SELECT *
         FROM runs
         WHERE pulsar = :pulsar
           AND status = "manual"
         ORDER BY COALESCE(run_generated_utc, imported_at_utc) DESC, id DESC
         LIMIT 1'
    );
    $stmt->execute([':pulsar' => $pulsar]);
    return $stmt->fetch() ?: null;
}

function list_active_manual_runs(array $config)
{
    $pdo = db_conn($config);
    $stmt = $pdo->query(
        'SELECT *
         FROM runs
         WHERE status = "manual"
         ORDER BY pulsar ASC, COALESCE(run_generated_utc, imported_at_utc) DESC, id DESC'
    );
    return $stmt->fetchAll();
}

function list_active_manual_runs_for_pulsar(PDO $pdo, $pulsar)
{
    $stmt = $pdo->prepare(
        'SELECT *
         FROM runs
         WHERE pulsar = :pulsar
           AND status = "manual"
         ORDER BY COALESCE(run_generated_utc, imported_at_utc) DESC, id DESC'
    );
    $stmt->execute([':pulsar' => $pulsar]);
    return $stmt->fetchAll();
}

function discard_run_due_to_manual_block(PDO $pdo, array $run, array $manualRun)
{
    $decisionAt = db_now_utc();
    $note = 'Discarded by system import because pulsar has active manual run ' . (string) $manualRun['run_id'];
    $stmt = $pdo->prepare(
        'UPDATE runs
         SET status = "discarded",
             decision_at_utc = :decision_at_utc,
             decision_by = :decision_by,
             postpone_until_utc = NULL,
             decision_note = :decision_note,
             merged_at_utc = NULL,
             merged_by = NULL,
             merge_note = NULL
         WHERE run_id = :run_id
           AND status = "pending"'
    );
    $stmt->execute([
        ':decision_at_utc' => $decisionAt,
        ':decision_by' => 'system_import',
        ':decision_note' => $note,
        ':run_id' => $run['run_id'],
    ]);

    return $stmt->rowCount() > 0;
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

    $importedAt = db_now_utc();
    $newRunSortUtc = trim((string) ($runGeneratedUtc ?? ''));
    if ($newRunSortUtc === '') {
        $newRunSortUtc = $importedAt;
    }

    $outdatedCount = 0;
    $blockedByManual = false;
    $blockingManualRunId = null;
    $inserted = false;
    $pdo->beginTransaction();
    try {
        $stmt->execute([
            ':run_id' => $runId,
            ':pulsar' => $pulsar,
            ':run_generated_utc' => $runGeneratedUtc,
            ':imported_at_utc' => $importedAt,
            ':manifest_path' => realpath($manifestPath) ?: $manifestPath,
            ':diagnostics_csv_path' => $diagnosticsCsvPath,
            ':output_tim_path' => $outputTimPath,
            ':diagnostic_plot_path' => $diagnosticPlotPath,
            ':trusted_observations' => isset($summary['trusted_observations']) ? (int) $summary['trusted_observations'] : null,
            ':new_observations' => isset($summary['new_observations']) ? (int) $summary['new_observations'] : null,
            ':best_particle_log_weight' => isset($summary['best_particle_log_weight']) ? (float) $summary['best_particle_log_weight'] : null,
        ]);

        $inserted = $stmt->rowCount() > 0;
        if ($inserted) {
            $outdatedCount = mark_older_pending_runs_outdated($pdo, $pulsar, $runId, $newRunSortUtc);
            $manualRun = find_active_manual_run_for_pulsar($pdo, $pulsar);
            if ($manualRun && (string) $manualRun['run_id'] !== $runId) {
                $blockedByManual = discard_run_due_to_manual_block($pdo, ['run_id' => $runId], $manualRun);
                $blockingManualRunId = (string) $manualRun['run_id'];
            }
        }
        $pdo->commit();
    } catch (Throwable $error) {
        $pdo->rollBack();
        throw $error;
    }

    return [
        'ok' => true,
        'inserted' => $inserted,
        'outdated' => $outdatedCount,
        'blocked_by_manual' => $blockedByManual,
        'blocking_manual_run_id' => $blockingManualRunId,
        'run_id' => $runId,
        'pulsar' => $pulsar,
    ];
}

function import_all_runs(array $config)
{
    $results = ['inserted' => 0, 'skipped' => 0, 'failed' => 0, 'outdated' => 0, 'details' => []];
    foreach (discover_manifest_paths($config['data_root']) as $manifestPath) {
        $result = import_manifest($config, $manifestPath);
        $results['details'][] = ['manifest' => $manifestPath, 'result' => $result];
        if (!$result['ok']) {
            $results['failed']++;
            continue;
        }
        $results['outdated'] += isset($result['outdated']) ? (int) $result['outdated'] : 0;
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

    if ((string) $run['status'] !== 'pending') {
        throw new RuntimeException('Only pending runs can be decided.');
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
                 decision_note = :decision_note,
                 merged_at_utc = NULL,
                 merged_by = NULL,
                 merge_note = NULL
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

        if ($action === 'accept') {
            $supersedeNote = 'Superseded by newer accepted run ' . (string) $runId;
            $supersedeStmt = $pdo->prepare(
                'UPDATE runs
                 SET status = "discarded",
                     decision_at_utc = :decision_at_utc,
                     decision_by = :decision_by,
                     postpone_until_utc = NULL,
                     decision_note = :decision_note,
                     merged_at_utc = NULL,
                     merged_by = NULL,
                     merge_note = NULL
                 WHERE pulsar = :pulsar
                   AND run_id <> :run_id
                   AND status = "accepted"'
            );
            $supersedeStmt->execute([
                ':decision_at_utc' => $decisionAt,
                ':decision_by' => $actor,
                ':decision_note' => $supersedeNote,
                ':pulsar' => $run['pulsar'],
                ':run_id' => $runId,
            ]);
        }

        $pdo->commit();
    } catch (Throwable $error) {
        $pdo->rollBack();
        throw $error;
    }
}

function mark_run_merged(array $config, $runId, $actor, $mergeNote)
{
    $pdo = db_conn($config);
    $stmt = $pdo->prepare('SELECT * FROM runs WHERE run_id = :run_id');
    $stmt->execute([':run_id' => $runId]);
    $run = $stmt->fetch();
    if (!$run) {
        throw new RuntimeException('Run not found.');
    }

    if ((string) $run['status'] !== 'accepted') {
        throw new RuntimeException('Only accepted runs can be marked merged.');
    }

    $mergedAt = db_now_utc();
    $noteValue = trim((string) $mergeNote);
    if ($noteValue === '') {
        $noteValue = null;
    }

    $update = $pdo->prepare(
        'UPDATE runs
         SET status = "merged",
             merged_at_utc = :merged_at_utc,
             merged_by = :merged_by,
             merge_note = :merge_note
         WHERE run_id = :run_id'
    );
    $update->execute([
        ':merged_at_utc' => $mergedAt,
        ':merged_by' => $actor,
        ':merge_note' => $noteValue,
        ':run_id' => $runId,
    ]);

    return find_run($config, $runId);
}

function clear_manual_run(array $config, $actor, $runId = null, $pulsar = null, $note = '')
{
    $pdo = db_conn($config);
    $run = null;

    if (is_string($runId) && trim($runId) !== '') {
        $stmt = $pdo->prepare('SELECT * FROM runs WHERE run_id = :run_id');
        $stmt->execute([':run_id' => trim($runId)]);
        $run = $stmt->fetch() ?: null;
    } elseif (is_string($pulsar) && trim($pulsar) !== '') {
        $manualRuns = list_active_manual_runs_for_pulsar($pdo, trim($pulsar));
        if (count($manualRuns) > 1) {
            throw new RuntimeException('Multiple active manual runs found for pulsar. Clear by run_id instead.');
        }
        $run = $manualRuns[0] ?? null;
    } else {
        throw new RuntimeException('Either run_id or pulsar is required.');
    }

    if (!$run) {
        throw new RuntimeException('Manual run not found.');
    }
    if ((string) $run['status'] !== 'manual') {
        throw new RuntimeException('Only manual runs can be cleared.');
    }

    $decisionAt = db_now_utc();
    $noteValue = trim((string) $note);
    if ($noteValue === '') {
        $noteValue = 'Manual follow-up cleared; run discarded while awaiting replacement.';
    }

    $update = $pdo->prepare(
        'UPDATE runs
         SET status = "manual_cleared",
             decision_at_utc = :decision_at_utc,
             decision_by = :decision_by,
             postpone_until_utc = NULL,
             decision_note = :decision_note,
             merged_at_utc = NULL,
             merged_by = NULL,
             merge_note = NULL
         WHERE run_id = :run_id
           AND status = "manual"'
    );
    $update->execute([
        ':decision_at_utc' => $decisionAt,
        ':decision_by' => $actor,
        ':decision_note' => $noteValue,
        ':run_id' => $run['run_id'],
    ]);
    if ($update->rowCount() < 1) {
        throw new RuntimeException('Failed to clear manual run.');
    }

    return find_run($config, $run['run_id']);
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

function compare_review_runs(array $left, array $right)
{
    $newComparison = ((int) $right['new_observations']) <=> ((int) $left['new_observations']);
    if ($newComparison !== 0) {
        return $newComparison;
    }

    return strcmp((string) $right['run_generated_utc'], (string) $left['run_generated_utc']);
}

function list_pending_runs_for_review(array $config)
{
    $pending = [];
    foreach (list_runs_with_rules($config) as $run) {
        if (run_is_eligible_for_review($run)) {
            $pending[] = $run;
        }
    }

    usort($pending, 'compare_review_runs');
    return $pending;
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

function find_next_pending_run_id(array $config, $currentRunId = null)
{
    $pending = list_pending_runs_for_review($config);
    if ($currentRunId === null || trim((string) $currentRunId) === '') {
        return isset($pending[0]['run_id']) ? (string) $pending[0]['run_id'] : null;
    }

    $currentRunId = trim((string) $currentRunId);
    foreach ($pending as $index => $run) {
        if ((string) $run['run_id'] === $currentRunId) {
            return isset($pending[$index + 1]['run_id']) ? (string) $pending[$index + 1]['run_id'] : null;
        }
    }

    return isset($pending[0]['run_id']) ? (string) $pending[0]['run_id'] : null;
}
