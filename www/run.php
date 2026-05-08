<?php
require_once __DIR__ . '/lib/bootstrap.php';
require_login();

$runId = isset($_GET['run_id']) ? (string) $_GET['run_id'] : '';
if ($runId === '') {
    http_response_code(400);
    echo 'Missing run_id';
    exit;
}

$run = find_run($APP_CONFIG, $runId);
if (!$run) {
  http_response_code(404);
  echo 'Run not found';
  exit;
}

$error = null;
$postponeInputValue = '';
$canDecide = ((string) $run['status']) === 'pending';
$nextPendingRunId = find_next_pending_run_id($APP_CONFIG, $runId);
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
  if (!$canDecide) {
    $error = 'This run is already terminal and cannot be changed.';
  }
    $action = isset($_POST['action']) ? (string) $_POST['action'] : '';
    $postponeRaw = isset($_POST['postpone_until']) ? trim((string) $_POST['postpone_until']) : '';
  $postponeInputValue = $postponeRaw;
    $postponeUtc = null;
  if ($error === null && $action === 'postpone') {
        if ($postponeRaw === '') {
            $error = 'Postpone date is required.';
        } else {
            $postponeUtc = $postponeRaw . 'T00:00:00Z';
        }
    }

    if ($error === null) {
        try {
          if ($action !== 'skip') {
            decide_run($APP_CONFIG, $runId, $action, (string) current_user(), $postponeUtc, $_POST['note'] ?? '');
          }
          if (is_string($nextPendingRunId) && $nextPendingRunId !== '') {
            header('Location: run.php?run_id=' . urlencode($nextPendingRunId));
          } else {
            header('Location: index.php');
          }
            exit;
        } catch (Throwable $err) {
            $error = $err->getMessage();
        }
    }
}

$rule = get_pulsar_rule($APP_CONFIG, $run['pulsar']);
$eligible = run_is_eligible_for_review($run + ['pulsar_postpone_until_utc' => $rule['postpone_until_utc'] ?? null]);
if ($postponeInputValue === '') {
        $postponeInputValue = gmdate('Y-m-d', strtotime('+60 days'));
}
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
    <p>Status: <span class="badge <?php echo htmlspecialchars($run['status']); ?>"><?php echo htmlspecialchars($run['status']); ?></span> Generated: <?php echo htmlspecialchars((string) $run['run_generated_utc']); ?></p>
    <p>Trusted observations: <?php echo (int) $run['trusted_observations']; ?>, New observations: <?php echo (int) $run['new_observations']; ?>, Best particle log weight: <?php echo htmlspecialchars((string) $run['best_particle_log_weight']); ?></p>
    <?php if ($rule && !empty($rule['postpone_until_utc'])): ?>
      <p>Current pulsar postpone-until: <?php echo htmlspecialchars($rule['postpone_until_utc']); ?></p>
    <?php endif; ?>
    <?php if ((string) $run['status'] === 'merged'): ?>
      <p>Merged at: <?php echo htmlspecialchars((string) $run['merged_at_utc']); ?> by <?php echo htmlspecialchars((string) $run['merged_by']); ?></p>
    <?php endif; ?>
  </div>
   <div class="card">
    <!--Review decision-->
    <?php if ($error): ?>
      <p style="color:#a52a2a"><?php echo htmlspecialchars($error); ?></p>
    <?php endif; ?>
    <?php if ($canDecide): ?>
      <form method="post">
        <div class="actions">
        <button name="action" value="skip" type="submit">Skip</button>

          <button name="action" value="accept" type="submit">Accept for merge</button>
          <button name="action" value="manual" type="submit">Flag manual intervention</button>
        
        <button name="action" value="postpone" type="submit">Postpone</button>
        
        <label>Postpone until
          <input type="date" name="postpone_until" value="<?php echo htmlspecialchars($postponeInputValue); ?>">
        </label>
        
    </div>
        <br>
        <label>Notes<br><textarea name="note" rows="3" cols="80"></textarea></label>
      </form>
    <?php else: ?>
      <?php if ((string) $run['status'] === 'outdated'): ?>
        <p>This run was automatically marked outdated because a newer run for this pulsar was imported. It is read-only.</p>
      <?php else: ?>
        <p>This run is in a terminal state and cannot be changed from the review UI.</p>
      <?php endif; ?>
    <?php endif; ?>
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

 
</main>
</body>
</html>
