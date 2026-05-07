<?php

function current_user()
{
    return isset($_SESSION['username']) ? (string) $_SESSION['username'] : null;
}

function is_logged_in()
{
    return current_user() !== null;
}

function require_login()
{
    if (!is_logged_in()) {
        header('Location: login.php');
        exit;
    }
}

function login_user(array $config, $username, $password)
{
    $expected = (string) $config['shared_password'];
    if ($expected === '' || $expected === 'change-me') {
        return 'Shared password is not configured. Set TIMING_WEB_PASSWORD.';
    }
    if (!is_string($password) || $password !== $expected) {
        return 'Invalid password.';
    }
    $clean = trim((string) $username);
    if ($clean === '') {
        $clean = 'reviewer';
    }
    $_SESSION['username'] = $clean;
    return null;
}

function logout_user()
{
    $_SESSION = [];
    if (ini_get('session.use_cookies')) {
        $params = session_get_cookie_params();
        setcookie(session_name(), '', time() - 42000, $params['path'], $params['domain'], $params['secure'], $params['httponly']);
    }
    session_destroy();
}
