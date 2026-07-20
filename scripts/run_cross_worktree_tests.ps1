[CmdletBinding()]
param(
    [string]$SourceRoot = "",
    [string]$PythonPath = "",
    [string]$TestPath = "toolchain/tests",
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = "Stop"

$testRepository = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$workspaceRoot = Split-Path -Parent $testRepository

if ([string]::IsNullOrWhiteSpace($SourceRoot)) {
    $SourceRoot = Join-Path $workspaceRoot "index"
}
$sourceRepository = (Resolve-Path -LiteralPath $SourceRoot).Path
$sourceToolchain = Join-Path $sourceRepository "toolchain"
$sourcePackage = Join-Path $sourceToolchain "esp_mcp_toolchain"
$serverScript = Join-Path $sourceToolchain "mcp_server.py"

if (-not (Test-Path -LiteralPath $sourcePackage -PathType Container)) {
    throw "ESP MCP package source was not found under: $sourcePackage"
}
if (-not (Test-Path -LiteralPath $serverScript -PathType Leaf)) {
    throw "MCP server entry point was not found: $serverScript"
}
if ($sourceRepository -eq $testRepository) {
    throw "SourceRoot must name the implementation worktree, not index-test."
}

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $condaPython = if ($env:CONDA_PREFIX) {
        $windowsPython = Join-Path $env:CONDA_PREFIX "python.exe"
        $posixPython = Join-Path $env:CONDA_PREFIX "python"
        if (Test-Path -LiteralPath $windowsPython -PathType Leaf) {
            $windowsPython
        } elseif (Test-Path -LiteralPath $posixPython -PathType Leaf) {
            $posixPython
        }
    }
    $PythonPath = if ($condaPython) {
        $condaPython
    } else {
        (Get-Command python -ErrorAction Stop).Source
    }
}
$resolvedPython = (Resolve-Path -LiteralPath $PythonPath).Path

$resolvedTestPath = if ([System.IO.Path]::IsPathRooted($TestPath)) {
    (Resolve-Path -LiteralPath $TestPath).Path
} else {
    (Resolve-Path -LiteralPath (Join-Path $testRepository $TestPath)).Path
}
$pytestConfig = Join-Path $testRepository "pyproject.toml"
$arguments = @(
    "-m",
    "pytest",
    "-c",
    $pytestConfig,
    "-o",
    "pythonpath=$sourceToolchain",
    "--import-mode=importlib",
    $resolvedTestPath
) + $PytestArgs

$previousSourceRoot = $env:ESP_MCP_SOURCE_ROOT
$previousPythonPath = $env:PYTHONPATH
$previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
try {
    $env:ESP_MCP_SOURCE_ROOT = $sourceRepository
    $env:PYTHONPATH = if ($previousPythonPath) {
        @($sourceToolchain, $previousPythonPath) -join [System.IO.Path]::PathSeparator
    } else {
        $sourceToolchain
    }
    $env:PYTHONDONTWRITEBYTECODE = "1"
    Write-Host "Python: $resolvedPython"
    Write-Host "Tests: $resolvedTestPath"
    Write-Host "Source: $sourceRepository"
    & $resolvedPython @arguments
    $exitCode = $LASTEXITCODE
} finally {
    $env:ESP_MCP_SOURCE_ROOT = $previousSourceRoot
    $env:PYTHONPATH = $previousPythonPath
    $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
}

exit $exitCode
