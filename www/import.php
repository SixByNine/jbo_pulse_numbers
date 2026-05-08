<?php
require_once __DIR__ . '/lib/bootstrap.php';
require_login();

$results = null;
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $results = import_all_runs($APP_CONFIG);
}
?>
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Import Runs</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
<header>
  <strong>Pulse Number Review</strong>
  <span style="float:right;"><a style="color:#fff" href="index.php">Dashboard</a></span>
</header>
<main>
  <div class="card">
    <h2>Import staged runs</h2>
    <p>Scans <?php echo htmlspecialchars($APP_CONFIG['data_root']); ?> for manifest.json files with a COMPLETE marker.</p>
    <form method="post"><button type="submit">Run import</button></form>
  </div>

  <?php if ($results !== null): ?>
    <div class="card">
      <h3>Import result</h3>
      <p>Inserted: <?php echo (int) $results['inserted']; ?>, Skipped: <?php echo (int) $results['skipped']; ?>, Failed: <?php echo (int) $results['failed']; ?>, Marked outdated: <?php echo (int) $results['outdated']; ?></p>
      <table>
        <tr><th>Manifest</th><th>Status</th></tr>
        <?php foreach ($results['details'] as $row): ?>
          <?php $result = $row['result']; ?>
          <tr>
            <td><?php echo htmlspecialchars($row['manifest']); ?></td>
            <td>
              <?php
              if (!$result['ok']) {
                  echo 'failed: ' . htmlspecialchars($result['reason']);
              } elseif (!empty($result['inserted'])) {
                  $outdated = isset($result['outdated']) ? (int) $result['outdated'] : 0;
                  echo 'inserted';
                  if ($outdated > 0) {
                    echo ' (marked outdated: ' . $outdated . ')';
                  }
              } else {
                  echo 'skipped';
              }
              ?>
            </td>
          </tr>
        <?php endforeach; ?>
      </table>
    </div>
  <?php endif; ?>
</main>
</body>
</html>
