param([string]$Python)

$ErrorActionPreference = 'Stop'
$SkillRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$VenvPython = Join-Path $SkillRoot '.venv\Scripts\python.exe'

function Find-Python {
    $Candidates = @()
    if ($Python) { $Candidates += $Python }
    $Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($Launcher) {
        try {
            $Launched = & $Launcher.Source -3 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0) { $Candidates += $Launched }
        } catch { }
    }
    foreach ($Name in @('python.exe', 'python3.exe')) {
        $Command = Get-Command $Name -ErrorAction SilentlyContinue
        if ($Command) { $Candidates += $Command.Source }
    }
    $Candidates += Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    foreach ($Candidate in ($Candidates | Select-Object -Unique)) {
        # Windows app-execution aliases may launch the Store instead of Python.
        if ($Candidate -match '[\\/]WindowsApps[\\/]python(?:3)?\.exe$') { continue }
        try {
            $Result = & $Candidate -c 'import sys; print(sys.executable); sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $Result) { return [string]($Result | Select-Object -Last 1) }
        } catch { }
    }
    throw 'Python 3.11+ was not found. Install Python, then rerun with -Python C:\path\to\python.exe.'
}

$PythonExecutable = Find-Python
$Node = Get-Command node.exe -ErrorAction SilentlyContinue
$Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $Node -or -not $Npm) { throw 'Node.js 22+ and npm are required. Install Node.js LTS, then rerun setup.' }
$NodeVersion = & $Node.Source --version
if ($NodeVersion -notmatch '^v(\d+)' -or [int]$Matches[1] -lt 22) { throw 'Node.js 22+ is required.' }
if (-not (Test-Path -LiteralPath (Join-Path $SkillRoot 'package-lock.json'))) { throw 'package-lock.json is missing from this skill package.' }

if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $PythonExecutable -m venv (Join-Path $SkillRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the skill-local Python environment.' }
}
& $VenvPython -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'The existing skill-local environment requires Python 3.11+. Recreate that environment with a supported Python.' }
& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $SkillRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Installing the pinned Python dependencies failed.' }
& $Npm.Source ci --prefix $SkillRoot --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'Installing the locked Node dependencies failed.' }

Push-Location -LiteralPath $SkillRoot
try {
    & $VenvPython -c 'import json, sys; sys.path.insert(0, "scripts"); from campus_homework.environment import doctor; print(json.dumps(doctor(), ensure_ascii=False, indent=2))'
    if ($LASTEXITCODE -ne 0) { throw 'The environment report could not run.' }
} finally {
    Pop-Location
}
