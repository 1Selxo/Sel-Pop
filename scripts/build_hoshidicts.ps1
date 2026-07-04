$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$hoshidictsRoot = Join-Path (Join-Path $repoRoot 'external') 'hoshidicts'
$buildRoot = Join-Path (Join-Path $repoRoot 'build') 'hoshidicts'
$outputRoot = Join-Path (Join-Path $repoRoot 'native') 'bin'

git -C $repoRoot submodule update --init --recursive
cmake -S (Join-Path $repoRoot 'native') -B $buildRoot
cmake --build $buildRoot --config Release --target sel-pop-hoshidicts-server --parallel

New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$source = Join-Path (Join-Path $buildRoot 'Release') 'sel-pop-hoshidicts-server.exe'
if (-not (Test-Path $source)) {
    $source = Join-Path $buildRoot 'sel-pop-hoshidicts-server'
}
Copy-Item -LiteralPath $source -Destination $outputRoot -Force
