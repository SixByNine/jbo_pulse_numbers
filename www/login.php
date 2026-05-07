<?php
require_once __DIR__ . '/lib/bootstrap.php';

if (is_logged_in()) {
    header('Location: index.php');
    exit;
}

$error = null;
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $error = login_user($APP_CONFIG, $_POST['username'] ?? '', $_POST['password'] ?? '');
    if ($error === null) {
        header('Location: index.php');
        exit;
    }
}
?>
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Login</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
<header><strong>Pulse Number Review</strong></header>
<main>
  <div class="card" style="max-width: 420px; margin: 40px auto;">
    <h2>Login</h2>
    <?php if ($error): ?>
      <p style="color: #a52a2a;"><?php echo htmlspecialchars($error); ?></p>
    <?php endif; ?>
    <form method="post">
      <label>Username<br><input type="text" name="username" placeholder="reviewer"></label><br>
      <label>Password<br><input type="password" name="password" required></label><br>
      <button type="submit">Sign in</button>
    </form>
  </div>
</main>
</body>
</html>
