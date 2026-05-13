<?php
require_once __DIR__ . '/lib/bootstrap.php';
require_login();

$runs = list_runs_with_rules($APP_CONFIG);
$pending = list_pending_runs_for_review($APP_CONFIG);
$blocked = [];
$decided = [];
$manualRuns = [];
$outdated = [];
$hiddenTerminal = [];
$errorRuns = [];

foreach ($runs as $run) {
  if ((string) $run['status'] === 'outdated') {
    $outdated[] = $run;
    continue;
  }
  if ((string) $run['status'] === 'error') {
    $errorRuns[] = $run;
    continue;
  }
  if (in_array((string) $run['status'], ['discarded', 'manual_cleared'], true)) {
    $hiddenTerminal[] = $run;
    continue;
  }
    if ((string) $run['status'] === 'pending') {
        if (!run_is_eligible_for_review($run)) {
            $blocked[] = $run;
        }
    } elseif ((string) $run['status'] === 'manual') {
      $manualRuns[] = $run;
    } else {
        $decided[] = $run;
    }
}

  usort($manualRuns, static function (array $left, array $right): int {
    $pulsarComparison = strcmp((string) $left['pulsar'], (string) $right['pulsar']);
    if ($pulsarComparison !== 0) {
      return $pulsarComparison;
    }

    $leftSort = (string) ($left['run_generated_utc'] ?? $left['imported_at_utc'] ?? '');
    $rightSort = (string) ($right['run_generated_utc'] ?? $right['imported_at_utc'] ?? '');
    $timeComparison = strcmp($rightSort, $leftSort);
    if ($timeComparison !== 0) {
      return $timeComparison;
    }

    return ((int) ($right['id'] ?? 0)) <=> ((int) ($left['id'] ?? 0));
  });
?>
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Pulse Number Review</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
<header>
  <strong>Pulse Number Review</strong>
  <span style="float:right;">
    <?php echo htmlspecialchars((string) current_user()); ?> |
    <a style="color:#fff" href="import.php">Import</a> |
    <a style="color:#fff" href="logout.php">Logout</a>
  </span>
</header>
<main>
  <div class="grid">
    <div class="card"><h3>Pending</h3><p><?php echo count($pending); ?></p></div>
    <div class="card"><h3>Blocked by postpone date</h3><p><?php echo count($blocked); ?></p></div>
    <div class="card"><h3>Decided</h3><p><?php echo count($decided); ?></p></div>
    <div class="card"><h3>Processing errors</h3><p><?php echo count($errorRuns); ?></p></div>
    <div class="card"><h3>Hidden terminal</h3><p><?php echo count($hiddenTerminal) + count($outdated); ?></p></div>
  </div>

  <div class="card">
    <h2>Ready for review</h2>
    <table>
      <tr><th>Pulsar</th><th>Run</th><th>Generated</th><th>Trusted</th><th>New</th><th>Action</th></tr>
      <?php foreach ($pending as $index => $run): ?>
        <tr<?php echo $index >= 20 ? ' class="review-row-hidden"' : ''; ?>>
          <td><?php echo htmlspecialchars($run['pulsar']); ?></td>
          <td><?php echo htmlspecialchars($run['run_id']); ?></td>
          <td><?php echo htmlspecialchars((string) $run['run_generated_utc']); ?></td>
          <td><?php echo (int) $run['trusted_observations']; ?></td>
          <td><?php echo (int) $run['new_observations']; ?></td>
          <td><a href="run.php?run_id=<?php echo urlencode($run['run_id']); ?>">Review</a></td>
        </tr>
      <?php endforeach; ?>
    </table>
    <?php if (count($pending) > 20): ?>
      <p><button type="button" id="show-more-review">More</button></p>
    <?php endif; ?>
  </div>

  <div class="card">
    <h2>Processing errors</h2>
    <table>
      <tr><th>Pulsar</th><th>Run</th><th>Error at</th><th>Message</th><th>Action</th></tr>
      <?php foreach ($errorRuns as $run): ?>
        <tr>
          <td><?php echo htmlspecialchars($run['pulsar']); ?></td>
          <td><?php echo htmlspecialchars($run['run_id']); ?></td>
          <td><?php echo htmlspecialchars((string) $run['decision_at_utc']); ?></td>
          <td><span class="note-preview" title="<?php echo htmlspecialchars((string) ($run['decision_note'] ?? '')); ?>"><?php echo htmlspecialchars((string) ($run['decision_note'] ?? '')); ?></span></td>
          <td><a href="run.php?run_id=<?php echo urlencode($run['run_id']); ?>">View</a></td>
        </tr>
      <?php endforeach; ?>
    </table>
  </div>

  <div class="card">
    <h2>Flagged for manual review</h2>
    <table>
      <tr><th>Pulsar</th><th>Run</th><th>Status</th><th>Decision at</th><th>By</th><th>Decision note</th><th>Action</th></tr>
      <?php foreach ($manualRuns as $run): ?>
        <tr>
          <td><?php echo htmlspecialchars($run['pulsar']); ?></td>
          <td><?php echo htmlspecialchars($run['run_id']); ?></td>
          <td><span class="badge <?php echo htmlspecialchars($run['status']); ?>"><?php echo htmlspecialchars($run['status']); ?></span></td>
          <td><?php echo htmlspecialchars((string) $run['decision_at_utc']); ?></td>
          <td><?php echo htmlspecialchars((string) $run['decision_by']); ?></td>
          <td><span class="note-preview" title="<?php echo htmlspecialchars((string) ($run['decision_note'] ?? '')); ?>"><?php echo htmlspecialchars((string) ($run['decision_note'] ?? '')); ?></span></td>
          <td><a href="run.php?run_id=<?php echo urlencode($run['run_id']); ?>">View</a></td>
        </tr>
      <?php endforeach; ?>
    </table>
  </div>

  <div class="card">
    <h2>Recently decided</h2>
    <table>
      <tr><th>Pulsar</th><th>Run</th><th>Status</th><th>Decision at</th><th>By</th><th>Action</th></tr>
      <?php foreach (array_slice($decided, 0, 50) as $run): ?>
        <tr>
          <td><?php echo htmlspecialchars($run['pulsar']); ?></td>
          <td><?php echo htmlspecialchars($run['run_id']); ?></td>
          <td><span class="badge <?php echo htmlspecialchars($run['status']); ?>"><?php echo htmlspecialchars($run['status']); ?></span></td>
          <td><?php echo htmlspecialchars((string) $run['decision_at_utc']); ?></td>
          <td><?php echo htmlspecialchars((string) $run['decision_by']); ?></td>
          <td><a href="run.php?run_id=<?php echo urlencode($run['run_id']); ?>">View</a></td>
        </tr>
      <?php endforeach; ?>
    </table>
  </div>

   <div class="card">
    <h2>Postponed Updates</h2>
    <table>
      <tr><th>Pulsar</th><th>Run</th><th>Generated</th><th>Postpone until</th><th>Action</th></tr>
      <?php foreach ($blocked as $run): ?>
        <tr>
          <td><?php echo htmlspecialchars($run['pulsar']); ?></td>
          <td><?php echo htmlspecialchars($run['run_id']); ?></td>
          <td><?php echo htmlspecialchars((string) $run['run_generated_utc']); ?></td>
          <td><?php echo htmlspecialchars((string) $run['pulsar_postpone_until_utc']); ?></td>
          <td><a href="run.php?run_id=<?php echo urlencode($run['run_id']); ?>">View</a></td>
        </tr>
      <?php endforeach; ?>
    </table>
  </div>
</main>
<script>
document.addEventListener('DOMContentLoaded', function () {
  var button = document.getElementById('show-more-review');
  if (!button) {
    return;
  }

  button.addEventListener('click', function () {
    document.querySelectorAll('.review-row-hidden').forEach(function (row) {
      row.classList.remove('review-row-hidden');
    });
    button.hidden = true;
  });
});
</script>
</body>
</html>
