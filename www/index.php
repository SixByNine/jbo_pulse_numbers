<?php
require_once __DIR__ . '/lib/bootstrap.php';
require_login();

$runs = list_runs_with_rules($APP_CONFIG);
$pending = [];
$blocked = [];
$decided = [];

foreach ($runs as $run) {
    if ((string) $run['status'] === 'pending') {
        if (run_is_eligible_for_review($run)) {
            $pending[] = $run;
        } else {
            $blocked[] = $run;
        }
    } else {
        $decided[] = $run;
    }
}
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
  </div>

  <div class="card">
    <h2>Pending review</h2>
    <table>
      <tr><th>Pulsar</th><th>Run</th><th>Generated</th><th>Trusted</th><th>New</th><th>Action</th></tr>
      <?php foreach ($pending as $run): ?>
        <tr>
          <td><?php echo htmlspecialchars($run['pulsar']); ?></td>
          <td><?php echo htmlspecialchars($run['run_id']); ?></td>
          <td><?php echo htmlspecialchars((string) $run['run_generated_utc']); ?></td>
          <td><?php echo (int) $run['trusted_observations']; ?></td>
          <td><?php echo (int) $run['new_observations']; ?></td>
          <td><a href="run.php?run_id=<?php echo urlencode($run['run_id']); ?>">Review</a></td>
        </tr>
      <?php endforeach; ?>
    </table>
  </div>

  <div class="card">
    <h2>Pending but not yet eligible</h2>
    <table>
      <tr><th>Pulsar</th><th>Run</th><th>Generated</th><th>Postpone until</th></tr>
      <?php foreach ($blocked as $run): ?>
        <tr>
          <td><?php echo htmlspecialchars($run['pulsar']); ?></td>
          <td><?php echo htmlspecialchars($run['run_id']); ?></td>
          <td><?php echo htmlspecialchars((string) $run['run_generated_utc']); ?></td>
          <td><?php echo htmlspecialchars((string) $run['pulsar_postpone_until_utc']); ?></td>
        </tr>
      <?php endforeach; ?>
    </table>
  </div>

  <div class="card">
    <h2>Recently decided</h2>
    <table>
      <tr><th>Pulsar</th><th>Run</th><th>Status</th><th>Decision at</th><th>By</th><th>Action</th></tr>
      <?php foreach (array_slice($decided, 0, 200) as $run): ?>
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
</main>
</body>
</html>
