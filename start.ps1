$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $PSScriptRoot

$taskCandidates = @()
if ($env:ULEARNING_PYTHON) { $taskCandidates += @{ Exe = $env:ULEARNING_PYTHON; Prefix = @() } }
if (Get-Command py -ErrorAction SilentlyContinue) { $taskCandidates += @{ Exe = 'py'; Prefix = @('-3') } }
if (Get-Command python -ErrorAction SilentlyContinue) { $taskCandidates += @{ Exe = 'python'; Prefix = @() } }
$taskBundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $taskBundledPython) { $taskCandidates += @{ Exe = $taskBundledPython; Prefix = @() } }

foreach ($taskCandidate in $taskCandidates) {
    $taskExe = $taskCandidate.Exe
    $taskPrefix = $taskCandidate.Prefix
    try {
        & $taskExe @taskPrefix -c 'import sys, tkinter; assert sys.version_info >= (3, 11); task_root=tkinter.Tk(); task_root.withdraw(); task_root.destroy()' 2>$null
        if ($LASTEXITCODE -eq 0) {
            & $taskExe @taskPrefix -m ulearning_assistant gui @args
            exit $LASTEXITCODE
        }
    } catch {
        # Try another already installed runtime. Nothing is installed automatically.
    }
}
Write-Host 'Python 3.11+ with Tkinter is required.'
Write-Host 'Install Python from https://www.python.org/downloads/windows/ or set ULEARNING_PYTHON.'
exit 1
