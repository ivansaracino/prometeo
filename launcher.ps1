# ════════════════════════════════════════════════════════════
# PROMETEO — Launcher silenzioso (no doppia apertura)
# Avvia il server Flask in background, attende che sia pronto,
# poi apre il browser direttamente sull'app. NESSUNA splash.
# ════════════════════════════════════════════════════════════

# Nasconde completamente la console PowerShell
Add-Type -Name Window -Namespace Console -MemberDefinition '
[DllImport("Kernel32.dll")]
public static extern IntPtr GetConsoleWindow();
[DllImport("User32.dll")]
public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
'
$consolePtr = [Console.Window]::GetConsoleWindow()
[Console.Window]::ShowWindow($consolePtr, 0)

# Working directory = cartella dello script
Set-Location -Path $PSScriptRoot

# ── Lock file: impedisce doppia apertura del browser entro 15 secondi ──
$lockFile = Join-Path $env:TEMP 'prometeo_browser.lock'
$canOpenBrowser = $true
if (Test-Path $lockFile) {
    try {
        $ageSec = ((Get-Date) - (Get-Item $lockFile).LastWriteTime).TotalSeconds
        if ($ageSec -lt 15) { $canOpenBrowser = $false }
    } catch {}
}

# ── Verifica se il server Flask è già in ascolto ──
function Test-ServerReady {
    try {
        $tcp = New-Object Net.Sockets.TcpClient
        $tcp.Connect('127.0.0.1', 5000)
        $isOpen = $tcp.Connected
        $tcp.Close()
        return $isOpen
    } catch {
        return $false
    }
}

$alreadyRunning = Test-ServerReady

if (-not $alreadyRunning) {
    # Avvia Python in background, totalmente invisibile
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "pythonw"
    $psi.Arguments = "run.py"
    $psi.WorkingDirectory = $PSScriptRoot
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

    try {
        [System.Diagnostics.Process]::Start($psi) | Out-Null
    } catch {
        # Fallback su python se pythonw non disponibile
        $psi.FileName = "python"
        try { [System.Diagnostics.Process]::Start($psi) | Out-Null } catch {}
    }

    # Attesa intelligente: polling porta 5000 (max 30 secondi)
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-ServerReady) {
            $ready = $true
            break
        }
    }
} else {
    $ready = $true
}

# ── Apri il browser SOLO se nessun'altra istanza l'ha già aperto ──
if ($ready -and $canOpenBrowser) {
    Set-Content -Path $lockFile -Value (Get-Date).Ticks -Force
    Start-Process "http://localhost:5000"
}
