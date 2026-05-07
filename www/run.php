<?php
require_once __DIR__ . '/lib/bootstrap.php';
require_login();

$runId = isset($_GET['run_id']) ? (string) $_GET['run_id'] : '';
if ($runId === '') {
    http_response_code(400);
    echo 'Missing run_id';
    exit;
}

$error = null;
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = isset($_POST['action']) ? (string) $_POST['action'] : '';
    $postponeRaw = isset($_POST['postpone_until']) ? trim((string) $_POST['postpone_until']) : '';
    $postponeUtc = null;
    if ($action === 'postpone') {
        if ($postponeRaw === '') {
            $error = 'Postpone date is required.';
        } else {
            $postponeUtc = $postponeRaw . 'T00:00:00Z';
        }
    }

    if ($error === null) {
        try {
            decide_run($APP_CONFIG, $runId, $action, (string) current_user(), $postponeUtc, $_POST['note'] ?? '');
            header('Location: run.php?run_id=' . urlencode($runId));
            exit;
        } catch (Throwable $err) {
            $error = $err->getMessage();
        }
    }
}

$run = find_run($APP_CONFIG, $runId);
if (!$run) {
    http_response_code(404);
    echo 'Run not found';
    exit;
}

$rule = get_pulsar_rule($APP_CONFIG, $run['pulsar']);
$eligible = run_is_eligible_for_review($run + ['pulsar_postpone_until_utc' => $rule['postpone_until_utc'] ?? null]);
?>
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Run <?php echo htmlspecialchars($run['run_id']); ?></title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
<header>
  <strong>Pulse Number Review</strong>
  <span style="float:right;"><a style="color:#fff" href="index.php">Dashboard</a></span>
</header>
<main>
  <div class="card">
    <h2><?php echo htmlspecialchars($run['pulsar']); ?> / <?php echo htmlspecialchars($run['run_id']); ?></h2>
    <p>Status: <span class="badge <?php echo htmlspecialchars($run['status']); ?>"><?php echo htmlspecialchars($run['status']); ?></span></p>
    <p>Generated: <?php echo htmlspecialchars((string) $run['run_generated_utc']); ?></p>
    <p>Trusted observations: <?php echo (int) $run['trusted_observations']; ?>, New observations: <?php echo (int) $run['new_observations']; ?></p>
    <p>Best particle log weight: <?php echo htmlspecialchars((string) $run['best_particle_log_weight']); ?></p>
    <?php if ($rule && !empty($rule['postpone_until_utc'])): ?>
      <p>Current pulsar postpone-until: <?php echo htmlspecialchars($rule['postpone_until_utc']); ?></p>
    <?php endif; ?>
    <p>Eligible for review: <?php echo $eligible ? 'yes' : 'no'; ?></p>
  </div>

  <?php if (!empty($run['diagnostic_plot_path'])): ?>
    <div class="card">
      <h3>Diagnostic plot</h3>
      <img class="plot" src="serve_artifact.php?run_id=<?php echo urlencode($run['run_id']); ?>&type=plot" alt="diagnostic plot">
    </div>
  <?php endif; ?>

  <div class="card">
    <h3>Artifacts</h3>
    <ul>
      <li><a href="serve_artifact.php?run_id=<?php echo urlencode($run['run_id']); ?>&type=csv">Diagnostics CSV</a></li>
      <li><a href="serve_artifact.php?run_id=<?php echo urlencode($run['run_id']); ?>&type=tim">Output TIM</a></li>
      <li><a href="serve_artifact.php?run_id=<?php echo urlencode($run['run_id']); ?>&type=manifest">Manifest JSON</a></li>
    </ul>
  </div>

  <div class="card">
    <h3>Review decision</h3>
    <?php if ($error): ?>
      <p style="color:#a52a2a"><?php echo htmlspecialchars($error); ?></p>
    <?php endif; ?>
    <form method="post">
      <div class="actions">
        <button name="action" value="accept" type="submit">Accept run</button>
        <button name="action" value="manual" type="submit">Flag manual intervention</button>
      </div>
      <label>Postpone until (UTC date)
        <input type="date" name="postpone_until">
      </label>
      <button name="action" value="postpone" type="submit">Postpone (discard this run)</button>
      <br>
      <label>Notes<br><textarea name="note" rows="3" cols="80"></textarea></label>
    </form>
  </div>
</main>
</body>
</html>
