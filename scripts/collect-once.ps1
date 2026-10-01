$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Ac = Join-Path $ProjectRoot ".venv\\Scripts\\ac.exe"
$LogDir = Join-Path $ProjectRoot "data\\logs"
$LogFile = Join-Path $LogDir "collector.log"

if (-not (Test-Path $Ac)) {
    throw "Azeroth Capital virtual environment not found at $Ac. Complete SETUP.md first."
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Push-Location $ProjectRoot
try {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$stamp] Starting commodity collection" | Tee-Object -FilePath $LogFile -Append
    & $Ac collect commodities 2>&1 | Tee-Object -FilePath $LogFile -Append
    if ($LASTEXITCODE -ne 0) {
        throw "Collector exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
