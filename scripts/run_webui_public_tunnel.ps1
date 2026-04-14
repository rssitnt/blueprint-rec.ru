param(
    [string]$Provider = "cloudflared-named",
    [switch]$KeepBackendRunning
)

$ErrorActionPreference = "Stop"

$repoRoot = "C:\projects\sites\blueprint-rec-2"
$webRoot = Join-Path $repoRoot "apps\web"
$pythonExe = "C:\Users\qwert\AppData\Local\Programs\Python\Python311\python.exe"
$cloudflaredExe = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$cloudflaredConfig = "C:\Users\qwert\.cloudflared\config.yml"

function Test-PortListening {
    param([int]$Port)
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    return [bool]$conn
}

function Test-FrontendHealthy {
    param([string]$BaseUrl = "http://127.0.0.1:3010")
    try {
        $homeResponse = Invoke-WebRequest -UseBasicParsing "$BaseUrl/" -TimeoutSec 10
        if ($homeResponse.StatusCode -ne 200 -or [string]::IsNullOrWhiteSpace($homeResponse.Content)) {
            return $false
        }
        $cssMatch = [regex]::Match($homeResponse.Content, 'href="([^"]+\.css)"')
        if (-not $cssMatch.Success) {
            return $false
        }
        $cssUrl = if ($cssMatch.Groups[1].Value.StartsWith("http")) {
            $cssMatch.Groups[1].Value
        } else {
            "$BaseUrl$($cssMatch.Groups[1].Value)"
        }
        $css = Invoke-WebRequest -UseBasicParsing $cssUrl -TimeoutSec 10
        if ($css.StatusCode -ne 200) {
            return $false
        }
        $contentType = [string]($css.Headers["Content-Type"])
        if ($contentType -notlike "text/css*") {
            return $false
        }
        return $true
    }
    catch {
        return $false
    }
}

function Test-BackendHealthy {
    param([string]$BaseUrl = "http://127.0.0.1:8010")
    try {
        $health = Invoke-WebRequest -UseBasicParsing "$BaseUrl/health" -TimeoutSec 10
        return $health.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Test-WebProductionBuild {
    $buildIdPath = Join-Path $webRoot ".next\BUILD_ID"
    $cssDir = Join-Path $webRoot ".next\static\css"
    if (-not (Test-Path $buildIdPath)) {
        return $false
    }
    if (-not (Test-Path $cssDir)) {
        return $false
    }
    $cssFile = Get-ChildItem -Path $cssDir -Recurse -Filter *.css -ErrorAction SilentlyContinue | Select-Object -First 1
    return [bool]$cssFile
}

function Stop-FrontendProcesses {
    $frontendProcesses = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -like "*next start*3010*" -or $_.CommandLine -like "*@blueprint-rec/web*3010*"
    }
    foreach ($proc in $frontendProcesses) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-BackendProcesses {
    $backendProcesses = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -like "*uvicorn*127.0.0.1*8010*" -or $_.CommandLine -like "*app.main:app*--port 8010*"
    }
    foreach ($proc in $backendProcesses) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Ensure-WebProductionBuild {
    if (Test-WebProductionBuild) {
        return
    }
    Push-Location $repoRoot
    try {
        & cmd.exe /c "npm run build --workspace @blueprint-rec/web"
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend production build failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

if (-not (Test-PortListening -Port 3010)) {
    Ensure-WebProductionBuild
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run start --workspace @blueprint-rec/web -- --port 3010" -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
} elseif (-not (Test-WebProductionBuild) -or -not (Test-FrontendHealthy)) {
    Stop-FrontendProcesses
    Start-Sleep -Seconds 2
    Ensure-WebProductionBuild
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run start --workspace @blueprint-rec/web -- --port 3010" -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
}

if (-not (Test-PortListening -Port 8010)) {
    Start-Process -FilePath $pythonExe -ArgumentList "-m","uvicorn","app.main:app","--app-dir","services/inference","--env-file","services/inference/.env.local","--host","127.0.0.1","--port","8010" -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
} elseif (-not (Test-BackendHealthy)) {
    Stop-BackendProcesses
    Start-Sleep -Seconds 2
    Start-Process -FilePath $pythonExe -ArgumentList "-m","uvicorn","app.main:app","--app-dir","services/inference","--env-file","services/inference/.env.local","--host","127.0.0.1","--port","8010" -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
}

$cf = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "cloudflared.exe" -and $_.CommandLine -like "*tunnel run blueprint-rec*"
} | Select-Object -First 1

if (-not $cf) {
    Start-Process -FilePath $cloudflaredExe -ArgumentList "--config",$cloudflaredConfig,"tunnel","run","blueprint-rec" -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
}
