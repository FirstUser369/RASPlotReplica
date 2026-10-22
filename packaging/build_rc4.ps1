param(
    [Parameter(Mandatory=$true)][string]$Destination,
    [Parameter(Mandatory=$true)][string]$RuntimeSource
)
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path $PSScriptRoot -Parent
$target = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
$runtimeSourceRoot = [IO.Path]::GetFullPath($RuntimeSource).TrimEnd('\')
if ((Split-Path $target -Leaf) -ne 'RAS-Plot-RC4') { throw 'Destination must be the named RAS-Plot-RC4 package.' }
if (Test-Path -LiteralPath $target) { throw 'RAS-Plot-RC4 already exists; refusing to overwrite it.' }
$sourcePython = Join-Path $runtimeSourceRoot 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $sourcePython)) { throw 'The approved portable runtime source is missing.' }
$stage = "$target.stage-$([guid]::NewGuid().ToString('N'))"
$sourceIdentity = Join-Path ([IO.Path]::GetTempPath()) ("rasplot-frontend-" + [guid]::NewGuid().ToString('N') + '.json')

& $sourcePython -B "$PSScriptRoot\release_manifest.py" source $sourceRoot $sourceIdentity
if ($LASTEXITCODE -ne 0) { throw 'Unable to fingerprint frontend source.' }
New-Item -ItemType Directory -Path "$stage\app\backend\app", "$stage\config" -Force | Out-Null

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
foreach ($document in 'README_RC4.md','RELEASE_NOTES_RC4.md','KNOWN_LIMITATIONS_RC4.md') {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $document) -Destination (Join-Path $stage $document)
}
Copy-Item -LiteralPath (Join-Path $runtimeSourceRoot 'runtime') -Destination (Join-Path $stage 'runtime') -Recurse

& $sourcePython -B "$PSScriptRoot\release_manifest.py" manifest $sourceRoot $stage $sourceIdentity
if ($LASTEXITCODE -ne 0) { throw 'Release manifest/build verification failed; RC4 was not created.' }
Remove-Item -LiteralPath $sourceIdentity
$payload = Get-ChildItem -LiteralPath $stage -Recurse -File | ForEach-Object {
    [PSCustomObject]@{path=$_.FullName.Substring($stage.Length+1); sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
}
$payload | ConvertTo-Json | Set-Content -LiteralPath "$stage\source-manifest.json" -Encoding UTF8
Move-Item -LiteralPath $stage -Destination $target
foreach ($entry in $payload) {
    if ((Get-FileHash -LiteralPath (Join-Path $target $entry.path) -Algorithm SHA256).Hash -ne $entry.sha256) {
        throw "Package hash mismatch: $($entry.path)"
    }
}
Write-Output "Built $target; verified $($payload.Count) payload hashes."
