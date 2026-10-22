param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path $PSScriptRoot -Parent
$target = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
if ((Split-Path $target -Leaf) -ne 'RAS-Plot' -or (Split-Path (Split-Path $target -Parent) -Leaf) -ne 'RAS-Plot Thursday Handoff') {
    throw 'Destination must be the named RAS-Plot Thursday Handoff\RAS-Plot package.'
}
if (-not (Test-Path -LiteralPath "$target\runtime\python\python.exe")) { throw 'Existing portable runtime is required.' }
$runtimePython = Join-Path $target 'runtime\python\python.exe'
$sourceIdentity = Join-Path ([IO.Path]::GetTempPath()) ("rasplot-frontend-" + [guid]::NewGuid().ToString('N') + '.json')
& $runtimePython -B "$PSScriptRoot\release_manifest.py" source $sourceRoot $sourceIdentity
if ($LASTEXITCODE -ne 0) { throw 'Unable to fingerprint frontend source.' }
Push-Location "$sourceRoot\frontend"
try {
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed; package unchanged.' }
} finally { Pop-Location }
$html = Get-Content -LiteralPath "$sourceRoot\backend\app\static\index.html" -Raw
if ($html -match 'unpkg\.com') { throw 'Production HTML contains unpkg.com.' }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stage = Join-Path $sourceRoot ".handoff-build-$stamp"
New-Item -ItemType Directory -Path "$stage\app\backend\app", "$stage\config" -Force | Out-Null
# Build application payload from source, never from the previous packaged app.
Get-ChildItem -LiteralPath "$sourceRoot\backend\app" -Recurse -File | Where-Object {
    $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and ($_.Extension -eq '.py' -or $_.FullName.StartsWith("$sourceRoot\backend\app\static\"))
} | ForEach-Object {
    $relative = $_.FullName.Substring("$sourceRoot\backend\app\".Length)
    $output = Join-Path "$stage\app\backend\app" $relative
    New-Item -ItemType Directory -Path (Split-Path $output -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $_.FullName -Destination $output
}
Copy-Item -LiteralPath "$sourceRoot\backend\requirements.txt" -Destination "$stage\app\backend\requirements.txt"
Copy-Item -LiteralPath "$PSScriptRoot\launcher.py" -Destination "$stage\app\launcher.py"
Copy-Item -LiteralPath "$PSScriptRoot\Start RAS-Plot.cmd" -Destination "$stage\Start RAS-Plot.cmd"
Copy-Item -LiteralPath "$sourceRoot\settings.package.json" -Destination "$stage\config\settings.local.json"
Copy-Item -LiteralPath "$sourceRoot\docs\support-matrix.md" -Destination "$stage\support-matrix.md"
& $runtimePython -B "$PSScriptRoot\release_manifest.py" manifest $sourceRoot $stage $sourceIdentity
if ($LASTEXITCODE -ne 0) { throw 'Release manifest/build verification failed; package unchanged.' }
Remove-Item -LiteralPath $sourceIdentity
$manifest = Get-ChildItem -LiteralPath $stage -Recurse -File | ForEach-Object {
    [PSCustomObject]@{path=$_.FullName.Substring($stage.Length+1); sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath "$stage\source-manifest.json" -Encoding UTF8

$parent = Split-Path $target -Parent
$backup = [IO.Path]::GetFullPath((Join-Path $parent "RAS-Plot-backup-$stamp"))
$nextApp = [IO.Path]::GetFullPath((Join-Path $target "app-next-$stamp"))
# Explicit containment checks before moving either application directory.
if (-not $backup.StartsWith("$parent\") -or -not $nextApp.StartsWith("$target\")) { throw 'Unsafe package path.' }
Copy-Item -LiteralPath "$stage\app" -Destination $nextApp -Recurse
New-Item -ItemType Directory -Path $backup -Force | Out-Null
Copy-Item -LiteralPath "$target\config" -Destination "$backup\config" -Recurse
Copy-Item -LiteralPath "$target\Start RAS-Plot.cmd" -Destination "$backup\Start RAS-Plot.cmd"
Move-Item -LiteralPath "$target\app" -Destination "$backup\app"
try {
    Move-Item -LiteralPath $nextApp -Destination "$target\app"
    Copy-Item -LiteralPath "$stage\config\settings.local.json" -Destination "$target\config\settings.local.json" -Force
    Copy-Item -LiteralPath "$stage\Start RAS-Plot.cmd" -Destination "$target\Start RAS-Plot.cmd" -Force
    Copy-Item -LiteralPath "$stage\source-manifest.json" -Destination "$target\source-manifest.json" -Force
    Copy-Item -LiteralPath "$stage\release-manifest.json" -Destination "$target\release-manifest.json" -Force
    Copy-Item -LiteralPath "$stage\support-matrix.md" -Destination "$target\support-matrix.md" -Force
} catch {
    if (-not (Test-Path -LiteralPath "$target\app")) { Move-Item -LiteralPath "$backup\app" -Destination "$target\app" }
    throw
}
foreach ($entry in $manifest) {
    if ((Get-FileHash -LiteralPath (Join-Path $target $entry.path) -Algorithm SHA256).Hash -ne $entry.sha256) { throw "Package hash mismatch: $($entry.path)" }
}
Write-Output "Rebuilt $target from source; verified $($manifest.Count) payload hashes."
Write-Output "Portable runtime retained. Previous app/config/launcher preserved at $backup."
