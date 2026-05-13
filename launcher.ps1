# Nascondi completamente PowerShell
Add-Type -Name Window -Namespace Console -MemberDefinition '
[DllImport("Kernel32.dll")]
public static extern IntPtr GetConsoleWindow();
[DllImport("User32.dll")]
public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
'

$consolePtr = [Console.Window]::GetConsoleWindow()
[Console.Window]::ShowWindow($consolePtr, 0)

# Crea finestra HTML professionale con il nuovo design
$html = @"
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <title>PROMETEO | Enterprise</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            min-height: 100vh;
            background: #050505; /* Nero assoluto */
            /* Effetto Marmo Scuro sul fondo */
            background-image: 
                radial-gradient(circle at 50% 80%, rgba(20, 20, 25, 0.8), transparent),
                linear-gradient(to bottom, transparent 60%, rgba(10, 10, 10, 0.9));
            font-family: 'Segoe UI', 'Orbitron', sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            overflow: hidden;
        }
        
        .main-stage {
            text-align: center;
            position: relative;
            animation: emerge 1.5s ease-out;
        }

        /* Fiaccola Bianca Marmorea (SVG) */
        .torch-container {
            width: 100px;
            margin: 0 auto 40px auto;
            filter: drop-shadow(0 0 25px rgba(255, 255, 255, 0.15));
        }

        .flame {
            fill: #fffbe6;
            animation: flicker 2s infinite alternate;
        }

        /* Tipografia Quadrata e Moderna */
        h1 {
            font-size: 64px;
            font-weight: 800;
            letter-spacing: 12px;
            color: #e0e0e0;
            margin-bottom: 5px;
            text-transform: uppercase;
            font-family: 'Verdana', sans-serif; /* Font più squadrato */
        }
        
        .divider {
            width: 300px;
            height: 2px;
            background: linear-gradient(90deg, transparent, #ffffff, transparent);
            margin: 15px auto;
            opacity: 0.6;
        }
        
        .subtitle {
            font-size: 12px;
            color: #888;
            letter-spacing: 6px;
            font-weight: 400;
            text-transform: uppercase;
        }

        /* Animazioni */
        @keyframes emerge {
            from { opacity: 0; filter: blur(10px); transform: scale(0.95); }
            to { opacity: 1; filter: blur(0); transform: scale(1); }
        }

        @keyframes flicker {
            0% { opacity: 0.8; transform: scale(1); }
            100% { opacity: 1; transform: scale(1.05); }
        }

        /* Riflesso sul piano (Effetto Marmo) */
        .reflection {
            position: absolute;
            bottom: -100px;
            left: 50%;
            transform: translateX(-50%) scaleY(-1);
            opacity: 0.1;
            filter: blur(4px);
            width: 100%;
        }
    </style>
</head>
<body>
    <div class="main-stage">
        <div class="torch-container">
            <svg viewBox="0 0 100 150" xmlns="http://www.w3.org/2000/svg">
                <!-- Base della fiaccola (Bianco Marmo) -->
                <path d="M40 140 L60 140 L55 80 L45 80 Z" fill="#f0f0f0" />
                <path d="M35 80 L65 80 L70 60 L30 60 Z" fill="#ffffff" />
                <!-- Fiamma Chiara -->
                <path class="flame" d="M50 10 C35 35 40 55 50 60 C60 55 65 35 50 10" />
            </svg>
        </div>
        
        <h1>PROMETEO</h1>
        <div class="divider"></div>
        <div class="subtitle">Enterprise Software Solutions</div>
    </div>
</body>
</html>
"@

# Salva e apri la finestra temporanea
$tempHtml = [System.IO.Path]::GetTempFileName() + ".html"
$html | Out-File -FilePath $tempHtml -Encoding UTF8
Start-Process $tempHtml

# Avvia Python in background (Logica originale)
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "python"
$psi.Arguments = "app.py"
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $false
$psi.RedirectStandardError = $false
$psi.CreateNoWindow = $true
$psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

$process = [System.Diagnostics.Process]::Start($psi)

# Attesa e apertura Browser
Start-Sleep -Seconds 2

$browserOpen = Get-Process | Where-Object { $_.ProcessName -like "*chrome*" -or $_.ProcessName -like "*firefox*" -or $_.ProcessName -like "*edge*" -or $_.ProcessName -like "*msedge*" }

if (-not $browserOpen) {
    Start-Process "http://localhost:5000"
}

$process.WaitForExit()