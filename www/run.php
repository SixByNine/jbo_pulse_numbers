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
$canEditManualNote = ((string) $run['status']) === 'manual';
$decisionNote = trim((string) ($run['decision_note'] ?? ''));
$manualNoteInputValue = $decisionNote;
$relatedActiveRuns = [];
if (!$canDecide) {
  $relatedActiveRuns = list_runs_for_pulsar_with_statuses(
    $APP_CONFIG,
    (string) $run['pulsar'],
    ['pending', 'accepted', 'postponed', 'manual'],
    $runId
  );
}
$nextPendingRunId = find_next_pending_run_id($APP_CONFIG, $runId);
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = isset($_POST['action']) ? (string) $_POST['action'] : '';
    $postponeRaw = isset($_POST['postpone_until']) ? trim((string) $_POST['postpone_until']) : '';
  $manualNoteInputValue = isset($_POST['note']) ? trim((string) $_POST['note']) : $manualNoteInputValue;
  $postponeInputValue = $postponeRaw;
    $postponeUtc = null;

  if ($action === 'update_manual_note') {
    if (!$canEditManualNote) {
      $error = 'Only manual runs can have their note edited.';
    }
  } elseif (!$canDecide) {
    $error = 'This run is already terminal and cannot be changed.';
  }

  if ($error === null && $action === 'postpone') {
        if ($postponeRaw === '') {
            $error = 'Postpone date is required.';
        } else {
            $postponeUtc = $postponeRaw . 'T00:00:00Z';
        }
    }

    if ($error === null) {
        try {
          if ($action === 'update_manual_note') {
            update_manual_decision_note($APP_CONFIG, $runId, $_POST['note'] ?? '');
            header('Location: run.php?run_id=' . urlencode($runId));
          } elseif ($action !== 'skip') {
            decide_run($APP_CONFIG, $runId, $action, (string) current_user(), $postponeUtc, $_POST['note'] ?? '');
            if (is_string($nextPendingRunId) && $nextPendingRunId !== '') {
            header('Location: run.php?run_id=' . urlencode($nextPendingRunId));
            } else {
            header('Location: index.php');
            }
          } elseif (is_string($nextPendingRunId) && $nextPendingRunId !== '') {
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
    <?php if ((string) $run['status'] === 'error'): ?>
      <p style="color:#a52a2a"><strong>Processing error:</strong> <?php echo htmlspecialchars($decisionNote); ?></p>
    <?php elseif ($decisionNote !== ''): ?>
      <p><strong>Decision note:</strong> <?php echo htmlspecialchars($decisionNote); ?></p>
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
    <?php elseif ($canEditManualNote): ?>
      <p>This run is flagged for manual intervention. You can update the note here while the run remains blocked for automated processing.</p>
      <form method="post">
        <input type="hidden" name="action" value="update_manual_note">
        <label>Manual follow-up note<br><textarea name="note" rows="5" cols="80" maxlength="5000"><?php echo htmlspecialchars($manualNoteInputValue); ?></textarea></label>
        <div class="actions">
          <button type="submit">Update note</button>
        </div>
      </form>
    <?php else: ?>
      <?php if ((string) $run['status'] === 'outdated'): ?>
        <p>This run was automatically marked outdated because a newer run for this pulsar was imported. It is read-only.</p>
      <?php else: ?>
        <p>This run is in a terminal state and cannot be changed from the review UI.</p>
      <?php endif; ?>
    <?php endif; ?>
    <?php if (!$canDecide): ?>
      <h3>Other active runs for this pulsar</h3>
      <?php if (!empty($relatedActiveRuns)): ?>
        <ul>
          <?php foreach ($relatedActiveRuns as $relatedRun): ?>
            <li>
              <a href="run.php?run_id=<?php echo urlencode((string) $relatedRun['run_id']); ?>"><?php echo htmlspecialchars((string) $relatedRun['run_id']); ?></a>
              <span class="badge <?php echo htmlspecialchars((string) $relatedRun['status']); ?>"><?php echo htmlspecialchars((string) $relatedRun['status']); ?></span>
              Generated: <?php echo htmlspecialchars((string) $relatedRun['run_generated_utc']); ?>
            </li>
          <?php endforeach; ?>
        </ul>
      <?php else: ?>
        <p>No other active runs for this pulsar.</p>
      <?php endif; ?>
    <?php endif; ?>
  </div>

  <?php if (!empty($run['diagnostic_plot_path'])): ?>
    <div class="card">
      <h3>Diagnostic plot</h3>
      <img class="plot" src="serve_artifact.php?run_id=<?php echo urlencode($run['run_id']); ?>&type=plot" alt="diagnostic plot">
    </div>
  <?php endif; ?>

  <?php if ((string) $run['status'] !== 'error'): ?>
  <div class="card">
    <h3>Artifacts</h3>
    <ul>
      <li><a href="serve_artifact.php?run_id=<?php echo urlencode($run['run_id']); ?>&type=csv">Diagnostics CSV</a></li>
      <li><a href="serve_artifact.php?run_id=<?php echo urlencode($run['run_id']); ?>&type=tim">Output TIM</a></li>
      <li><a href="serve_artifact.php?run_id=<?php echo urlencode($run['run_id']); ?>&type=manifest">Manifest JSON</a></li>
    </ul>
  </div>
  <?php endif; ?>

 
</main>
</body>
</html>
