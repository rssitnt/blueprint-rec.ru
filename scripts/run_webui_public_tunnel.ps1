param(
    [string]$Provider = "cloudflared-named",
    [switch]$KeepBackendRunning
)

$ErrorActionPreference = "Stop"

$repoRoot = "C:\projects\sites\blueprint-rec-2"
$pythonExe = "C:\Users\qwert\AppData\Local\Programs\Python\Python311\python.exe"
$cloudflaredExe = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$cloudflaredConfig = "C:\Users\qwert\.cloudflared\config.yml"

function Test-PortListening {
    param([int]$Port)
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    return [bool]$conn
}

if (-not (Test-PortListening -Port 3010)) {
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run start --workspace @blueprint-rec/web -- --port 3010" -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
}

if (-not (Test-PortListening -Port 8010)) {
    Start-Process -FilePath $pythonExe -ArgumentList "-m","uvicorn","app.main:app","--app-dir","services/inference","--env-file","services/inference/.env.local","--host","127.0.0.1","--port","8010" -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
}

$cf = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "cloudflared.exe" -and $_.CommandLine -like "*tunnel run blueprint-rec*"
} | Select-Object -First 1

if (-not $cf) {
    Start-Process -FilePath $cloudflaredExe -ArgumentList "--config",$cloudflaredConfig,"tunnel","run","blueprint-rec" -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
}
