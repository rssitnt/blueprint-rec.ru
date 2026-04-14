param(
    [string]$Provider = "cloudflared-named",
    [switch]$KeepBackendRunning
)

$ErrorActionPreference = "Stop"

$repoRoot = "C:\projects\sites\blueprint-rec-2"
$webRoot = Join-Path $repoRoot "apps\web"
$homepageSmokeScript = Join-Path $repoRoot "scripts\verify_homepage_smoke.mjs"
$publicProxyScript = Join-Path $repoRoot "scripts\web_public_proxy.mjs"
$pythonExe = "C:\Users\qwert\AppData\Local\Programs\Python\Python311\python.exe"
$cloudflaredExe = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$cloudflaredConfig = "C:\Users\qwert\.cloudflared\config.yml"
$cloudflaredProtocol = "quic"
$frontendPort = 3010
$publicProxyPort = 3020

function Test-PortListening {
    param([int]$Port)
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    return [bool]$conn
}

function Test-FrontendHealthy {
    param([string]$BaseUrl = "http://127.0.0.1:$frontendPort")
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

function Invoke-FrontendSmokeCheck {
    param(
        [string]$BaseUrl = "http://127.0.0.1:$publicProxyPort",
        [string]$OutDir = "C:\projects\sites\blueprint-rec-2\.codex-smoke\startup-home-smoke"
    )
    if (-not (Test-Path $homepageSmokeScript)) {
        return $false
    }
    Push-Location $repoRoot
    try {
        & node $homepageSmokeScript --url "$BaseUrl/" --out-dir $OutDir | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
    finally {
        Pop-Location
    }
}

function Wait-FrontendHealthy {
    param([string]$BaseUrl = "http://127.0.0.1:$frontendPort")
    for ($i = 0; $i -lt 30; $i++) {
        if (Test-FrontendHealthy -BaseUrl $BaseUrl) {
            return $true
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Start-FrontendProcess {
    Ensure-WebProductionBuild
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run start --workspace @blueprint-rec/web -- --port $frontendPort" -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
}

function Test-PublicProxyHealthy {
    param([string]$BaseUrl = "http://127.0.0.1:$publicProxyPort")
    try {
        $homeResponse = Invoke-WebRequest -UseBasicParsing "$BaseUrl/" -TimeoutSec 10
        if ($homeResponse.StatusCode -ne 200 -or [string]::IsNullOrWhiteSpace($homeResponse.Content)) {
            return $false
        }
        $jsMatch = [regex]::Match($homeResponse.Content, 'src="([^"]+/_next/static/[^"]+\.js)"')
        if (-not $jsMatch.Success) {
            return $false
        }
        $jsUrl = if ($jsMatch.Groups[1].Value.StartsWith("http")) {
            $jsMatch.Groups[1].Value
        } else {
            "$BaseUrl$($jsMatch.Groups[1].Value)"
        }
        $js = Invoke-WebRequest -UseBasicParsing $jsUrl -TimeoutSec 15
        if ($js.StatusCode -ne 200) {
            return $false
        }
        $contentType = [string]($js.Headers["Content-Type"])
        return ($contentType -like "application/javascript*" -or $contentType -like "text/javascript*")
    }
    catch {
        return $false
    }
}

function Wait-PublicProxyHealthy {
    param([string]$BaseUrl = "http://127.0.0.1:$publicProxyPort")
    for ($i = 0; $i -lt 20; $i++) {
        if (Test-PublicProxyHealthy -BaseUrl $BaseUrl) {
            return $true
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Start-PublicProxyProcess {
    if (-not (Test-Path $publicProxyScript)) {
        throw "Public proxy script not found: $publicProxyScript"
    }
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "set BLUEPRINT_UPSTREAM_PORT=$frontendPort&& set BLUEPRINT_PUBLIC_PROXY_PORT=$publicProxyPort&& node $publicProxyScript" -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
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

function Get-FrontendProcess {
    return Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "node.exe" -and $_.CommandLine -like "*next*start*3010*"
    } | Select-Object -First 1
}

function Test-FrontendProcessOutdated {
    $buildIdPath = Join-Path $webRoot ".next\BUILD_ID"
    if (-not (Test-Path $buildIdPath)) {
        return $false
    }

    $frontendProcess = Get-FrontendProcess
    if (-not $frontendProcess) {
        return $false
    }

    $buildWriteTime = (Get-Item $buildIdPath).LastWriteTimeUtc
    if ([string]::IsNullOrWhiteSpace($frontendProcess.CreationDate)) {
        return $false
    }

    try {
        $processStartTime = [Management.ManagementDateTimeConverter]::ToDateTime($frontendProcess.CreationDate).ToUniversalTime()
    }
    catch {
        return $false
    }

    return $buildWriteTime -gt $processStartTime.AddSeconds(2)
}

function Stop-FrontendProcesses {
    $frontendProcesses = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "node.exe" -and (
            $_.CommandLine -like "*next*start*$frontendPort*" -or
            $_.CommandLine -like "*@blueprint-rec/web*$frontendPort*"
        )
    }
    foreach ($proc in $frontendProcesses) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-PublicProxyProcesses {
    $proxyProcesses = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -like "*web_public_proxy.mjs*" -or $_.CommandLine -like "*BLUEPRINT_PUBLIC_PROXY_PORT=$publicProxyPort*"
    }
    foreach ($proc in $proxyProcesses) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Ensure-CloudflaredConfig {
    if (-not (Test-Path $cloudflaredConfig)) {
        throw "Cloudflared config not found: $cloudflaredConfig"
    }
    $lines = [System.Collections.Generic.List[string]](Get-Content $cloudflaredConfig)
    $targetService = "service: http://127.0.0.1:$publicProxyPort"
    $changed = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match 'hostname:\s*blueprint-rec\.ru') {
            for ($j = $i + 1; $j -lt [Math]::Min($i + 6, $lines.Count); $j++) {
                if ($lines[$j] -match '^\s*service:\s*http://127\.0\.0\.1:\d+\s*$') {
                    if ($lines[$j].Trim() -ne $targetService) {
                        $indent = ([regex]::Match($lines[$j], '^\s*')).Value
                        $lines[$j] = "$indent$targetService"
                        $changed = $true
                    }
                    if ($changed) {
                        Set-Content -Path $cloudflaredConfig -Value $lines -Encoding UTF8
                    }
                    return $changed
                }
                if ($lines[$j] -match '^\s*-\s*service:') {
                    break
                }
            }
            throw "Could not find service line for blueprint-rec.ru in cloudflared config"
        }
    }
    throw "Could not find hostname blueprint-rec.ru in cloudflared config"
}

function Stop-CloudflaredProcesses {
    $cloudflaredProcesses = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "cloudflared.exe" -and $_.CommandLine -like "*tunnel run blueprint-rec*"
    }
    foreach ($proc in $cloudflaredProcesses) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Test-CloudflaredServiceRunning {
    $service = Get-Service -Name "cloudflared" -ErrorAction SilentlyContinue
    return [bool]($service -and $service.Status -eq "Running")
}

function Restart-CloudflaredService {
    try {
        $service = Get-Service -Name "cloudflared" -ErrorAction Stop
        if ($service.Status -eq "Running") {
            Restart-Service -Name "cloudflared" -Force -ErrorAction Stop
        } else {
            Start-Service -Name "cloudflared" -ErrorAction Stop
        }

        Start-Sleep -Seconds 3
        return $true
    }
    catch {
        return $false
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

if (-not (Test-PortListening -Port $frontendPort)) {
    Start-FrontendProcess
} elseif (-not (Test-WebProductionBuild) -or (Test-FrontendProcessOutdated) -or -not (Test-FrontendHealthy)) {
    Stop-FrontendProcesses
    Start-Sleep -Seconds 2
    Start-FrontendProcess
}

if (-not (Wait-FrontendHealthy)) {
    Stop-FrontendProcesses
    Start-Sleep -Seconds 2
    Start-FrontendProcess
    [void](Wait-FrontendHealthy)
}

if (-not (Invoke-FrontendSmokeCheck)) {
    Stop-FrontendProcesses
    Start-Sleep -Seconds 2
    Start-FrontendProcess
    if (Wait-FrontendHealthy) {
        [void](Invoke-FrontendSmokeCheck)
    }
}

if (-not (Test-PortListening -Port $publicProxyPort)) {
    Start-PublicProxyProcess
} elseif (-not (Test-PublicProxyHealthy)) {
    Stop-PublicProxyProcesses
    Start-Sleep -Seconds 2
    Start-PublicProxyProcess
}

if (-not (Wait-PublicProxyHealthy)) {
    Stop-PublicProxyProcesses
    Start-Sleep -Seconds 2
    Start-PublicProxyProcess
    [void](Wait-PublicProxyHealthy)
}

if (-not (Test-PortListening -Port 8010)) {
    Start-Process -FilePath $pythonExe -ArgumentList "-m","uvicorn","app.main:app","--app-dir","services/inference","--env-file","services/inference/.env.local","--host","127.0.0.1","--port","8010" -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
} elseif (-not (Test-BackendHealthy)) {
    Stop-BackendProcesses
    Start-Sleep -Seconds 2
    Start-Process -FilePath $pythonExe -ArgumentList "-m","uvicorn","app.main:app","--app-dir","services/inference","--env-file","services/inference/.env.local","--host","127.0.0.1","--port","8010" -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
}

$cloudflaredConfigChanged = Ensure-CloudflaredConfig

if ($cloudflaredConfigChanged -and -not (Restart-CloudflaredService)) {
    Stop-CloudflaredProcesses
    Start-Sleep -Seconds 2
}

if (-not (Test-CloudflaredServiceRunning)) {
    $cf = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "cloudflared.exe" -and $_.CommandLine -like "*tunnel run blueprint-rec*"
    } | Select-Object -First 1

    if (-not $cf) {
        Start-Process -FilePath $cloudflaredExe -ArgumentList "--config",$cloudflaredConfig,"tunnel","--protocol",$cloudflaredProtocol,"run","blueprint-rec" -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
    }
}
