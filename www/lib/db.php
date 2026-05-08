<?php

function db_now_utc()
{
    return gmdate('Y-m-d\TH:i:s\Z');
}

function db_conn(array $config)
{
    static $pdo = null;
    if ($pdo instanceof PDO) {
        return $pdo;
    }

    $dbPath = $config['sqlite_path'];
    $dbDir = dirname($dbPath);
    if (!is_dir($dbDir)) {
        mkdir($dbDir, 0775, true);
    }

    $pdo = new PDO('sqlite:' . $dbPath);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
    $pdo->exec('PRAGMA journal_mode = WAL');
    return $pdo;
}

function db_column_exists(PDO $pdo, $table, $column)
{
    $stmt = $pdo->query('PRAGMA table_info(' . $table . ')');
    $columns = $stmt->fetchAll();
    foreach ($columns as $info) {
        if (isset($info['name']) && (string) $info['name'] === (string) $column) {
            return true;
        }
    }
    return false;
}

function db_ensure_column(PDO $pdo, $table, $column, $definition)
{
    if (db_column_exists($pdo, $table, $column)) {
        return;
    }
    $pdo->exec('ALTER TABLE ' . $table . ' ADD COLUMN ' . $column . ' ' . $definition);
}

function init_db(array $config)
{
    $pdo = db_conn($config);

    $pdo->exec(
        'CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL UNIQUE,
            pulsar TEXT NOT NULL,
            run_generated_utc TEXT,
            imported_at_utc TEXT NOT NULL,
            manifest_path TEXT NOT NULL,
            diagnostics_csv_path TEXT,
            output_tim_path TEXT,
            diagnostic_plot_path TEXT,
            trusted_observations INTEGER,
            new_observations INTEGER,
            best_particle_log_weight REAL,
            status TEXT NOT NULL DEFAULT "pending",
            decision_at_utc TEXT,
            decision_by TEXT,
            postpone_until_utc TEXT,
            decision_note TEXT,
            merged_at_utc TEXT,
            merged_by TEXT,
            merge_note TEXT
        )'
    );

    $pdo->exec(
        'CREATE TABLE IF NOT EXISTS pulsar_rules (
            pulsar TEXT PRIMARY KEY,
            postpone_until_utc TEXT,
            source_run_id TEXT,
            updated_at_utc TEXT NOT NULL,
            updated_by TEXT
        )'
    );

    $pdo->exec('CREATE INDEX IF NOT EXISTS idx_runs_pulsar ON runs (pulsar)');
    $pdo->exec('CREATE INDEX IF NOT EXISTS idx_runs_status ON runs (status)');
    $pdo->exec('CREATE INDEX IF NOT EXISTS idx_runs_generated ON runs (run_generated_utc)');

    // Migration path for databases created before merged-state columns existed.
    db_ensure_column($pdo, 'runs', 'merged_at_utc', 'TEXT');
    db_ensure_column($pdo, 'runs', 'merged_by', 'TEXT');
    db_ensure_column($pdo, 'runs', 'merge_note', 'TEXT');
}
