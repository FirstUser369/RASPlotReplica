param(
    [Parameter(Mandatory=$true)][string]$Destination,
    [Parameter(Mandatory=$true)][string]$RuntimeSource,
    [string]$ZipPath = ""
)
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path $PSScriptRoot -Parent
$target = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
$runtimeSourceRoot = [IO.Path]::GetFullPath($RuntimeSource).TrimEnd('\')
if ((Split-Path $target -Leaf) -ne 'RAS-Plot-Team-Review') { throw 'Destination folder must be named RAS-Plot-Team-Review.' }
if (Test-Path -LiteralPath $target) { throw 'Team-review destination already exists; refusing to overwrite it.' }
$sourcePython = Join-Path $runtimeSourceRoot 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $sourcePython)) { throw 'The approved portable runtime source is missing.' }
$stage = "$target.stage-$([guid]::NewGuid().ToString('N'))"
$sourceIdentity = Join-Path ([IO.Path]::GetTempPath()) ("rasplot-frontend-" + [guid]::NewGuid().ToString('N') + '.json')
$safeBuild = Join-Path ([IO.Path]::GetTempPath()) ("rasplot-build-" + [guid]::NewGuid().ToString('N'))
try {
    & $sourcePython -B "$PSScriptRoot\release_manifest.py" source $sourceRoot $sourceIdentity
    if ($LASTEXITCODE -ne 0) { throw 'Unable to fingerprint frontend source.' }

    # Some managed Windows systems block executables launched from OneDrive.
    # Run the project's existing esbuild binary from a private local temp path;
    # no package is installed or downloaded.
    $sourceEsbuild = Join-Path $sourceRoot 'frontend\node_modules\@esbuild\win32-x64\esbuild.exe'
    if (-not (Test-Path -LiteralPath $sourceEsbuild)) { throw 'Existing project esbuild binary is missing.' }
    New-Item -ItemType Directory -Path $safeBuild -Force | Out-Null
    $safeEsbuild = Join-Path $safeBuild 'esbuild.exe'
    Copy-Item -LiteralPath $sourceEsbuild -Destination $safeEsbuild
    $previousEsbuild = $env:ESBUILD_BINARY_PATH
    $env:ESBUILD_BINARY_PATH = $safeEsbuild
    Push-Location "$sourceRoot\frontend"
    try { & npm run build } finally {
        Pop-Location
        $env:ESBUILD_BINARY_PATH = $previousEsbuild
    }
    if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed.' }

    New-Item -ItemType Directory -Path "$stage\app\backend\app", "$stage\config" -Force | Out-Null
    Get-ChildItem -LiteralPath "$sourceRoot\backend\app" -Recurse -File | Where-Object {
        $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and
        ($_.Extension -eq '.py' -or $_.FullName.StartsWith("$sourceRoot\backend\app\static\"))
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
    Copy-Item -LiteralPath "$PSScriptRoot\README_TEAM_REVIEW.md" -Destination "$stage\README - Start Here.md"
    Copy-Item -LiteralPath "$PSScriptRoot\FEEDBACK_TEMPLATE.md" -Destination "$stage\Feedback Template.md"
    Copy-Item -LiteralPath "$sourceRoot\docs\support-matrix.md" -Destination "$stage\support-matrix.md"
    Copy-Item -LiteralPath "$sourceRoot\KNOWN_LIMITATIONS_RC4.md" -Destination "$stage\known-limitations.md"
    Copy-Item -LiteralPath (Join-Path $runtimeSourceRoot 'runtime') -Destination (Join-Path $stage 'runtime') -Recurse

    & $sourcePython -B "$PSScriptRoot\release_manifest.py" manifest $sourceRoot $stage $sourceIdentity `
        'TEAM-REVIEW' 'Engineering Team Review' 'Current uncommitted working tree; local testing only'
    if ($LASTEXITCODE -ne 0) { throw 'Release manifest/build verification failed.' }
    $payload = Get-ChildItem -LiteralPath $stage -Recurse -File | ForEach-Object {
        [PSCustomObject]@{path=$_.FullName.Substring($stage.Length+1); sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
    }
    $payload | ConvertTo-Json | Set-Content -LiteralPath "$stage\package-integrity.json" -Encoding UTF8
    Move-Item -LiteralPath $stage -Destination $target
    foreach ($entry in $payload) {
        if ((Get-FileHash -LiteralPath (Join-Path $target $entry.path) -Algorithm SHA256).Hash -ne $entry.sha256) {
            throw "Package hash mismatch: $($entry.path)"
        }
    }
    if (-not $ZipPath) { $ZipPath = "$target.zip" }
    if (Test-Path -LiteralPath $ZipPath) { throw 'Team-review ZIP already exists; refusing to overwrite it.' }
    Compress-Archive -LiteralPath $target -DestinationPath $ZipPath -CompressionLevel Optimal
    Write-Output "Built $target and $ZipPath; verified $($payload.Count) payload hashes."
}
finally {
    if (Test-Path -LiteralPath $sourceIdentity) { Remove-Item -LiteralPath $sourceIdentity }
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
    if (Test-Path -LiteralPath $safeBuild) { Remove-Item -LiteralPath $safeBuild -Recurse -Force }
}
