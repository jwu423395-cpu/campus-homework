#requires -Version 7.0
param(
    [string]$Version = '0.1.0',
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$') { throw 'Use a plain semantic version.' }
$SourceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$OutputRoot = [IO.Path]::GetFullPath($OutputDirectory)
if ($OutputRoot.Equals($SourceRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $OutputRoot.StartsWith($SourceRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Release output must be outside the skill source directory.'
}
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$RootNames = @('.gitignore', 'LICENSE', 'README.md', 'SKILL.md', 'package.json', 'package-lock.json', 'requirements.txt')
$Directories = @('agents', '.github', 'docs', 'examples', 'references', 'scripts', 'tests', 'assets')
$ExcludedParts = '(?i)(^|/)(\.venv|node_modules|tmp|private|__pycache__|output|runs|\.git|\.pytest_cache)(/|$)'
$Candidates = @()
foreach ($Name in $RootNames) {
    $Path = Join-Path $SourceRoot $Name
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Required root file missing: $Name" }
    $Candidates += Get-Item -LiteralPath $Path
}
foreach ($Name in $Directories) {
    $Path = Join-Path $SourceRoot $Name
    if (Test-Path -LiteralPath $Path) { $Candidates += Get-ChildItem -LiteralPath $Path -File -Recurse -Force }
}

$Files = @()
foreach ($File in $Candidates) {
    $Relative = [IO.Path]::GetRelativePath($SourceRoot, $File.FullName).Replace('\', '/')
    if ($Relative -match $ExcludedParts) { continue }
    if ($File.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Do not package symbolic links: $Relative" }
    $Extension = $File.Extension.ToLowerInvariant()
    if ($Relative -notin $RootNames -and $Extension -notin @('.py','.ps1','.mjs','.json','.md','.txt','.yaml','.yml','.html','.png','.jpg','.jpeg','.webp','.ttf','.otf')) {
        throw "Unreviewed file type in public directories: $Relative"
    }
    if ($Relative -match '(?i)(^|/)(?:\.env(?:\..*)?|profile\.json|.*\.(?:log|token|sqlite|db|zip|pyc|pyd|exe|dll))$') {
        throw "Private/runtime file cannot be released: $Relative"
    }
    if ($Relative -like 'assets/*' -and $Relative -notmatch '^assets/(fonts|backgrounds|samples)/') {
        throw "Unreviewed asset directory: $Relative"
    }
    if ($Relative -match '^examples/.*-task\.json$') {
        $Task = Get-Content -LiteralPath $File.FullName -Raw | ConvertFrom-Json
        foreach ($Question in $Task.questions) {
            if ($Question.source.kind -ne 'synthetic' -or $Question.source.complete -ne $true) {
                throw "Only complete self-authored task examples may be packaged: $Relative"
            }
        }
    }
    if ($Extension -in @('.py','.ps1','.mjs','.json','.md','.txt','.yaml','.yml','.html') -or $Relative -eq '.gitignore') {
        $Text = [IO.File]::ReadAllText($File.FullName)
        $PrivatePatterns = @(
            '(?i)[A-Z]:[\\/](?:Users|Documents and Settings)[\\/][^\s"<>]+',
            '\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}',
            '\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})',
            '-----BEGIN (?:RSA|EC|OPENSSH) PRIVATE KEY-----'
        )
        foreach ($Pattern in $PrivatePatterns) {
            if ($Text -match $Pattern) { throw "Potential personal path or credential in $Relative; inspect locally (value withheld)." }
        }
    }
    $Files += [PSCustomObject]@{path=$Relative; bytes=$File.Length; sha256=(Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
}
$Files = @($Files | Sort-Object -Property path)
if (-not $Files.Count) { throw 'No public files were selected.' }
if (-not (Select-String -LiteralPath (Join-Path $SourceRoot 'LICENSE') -SimpleMatch 'MIT License' -Quiet)) { throw 'MIT license is missing.' }
$FontRoot = Join-Path $SourceRoot 'assets\fonts'
$Fonts = Get-Content -LiteralPath (Join-Path $FontRoot 'manifest.json') -Raw | ConvertFrom-Json
foreach ($Font in $Fonts.PSObject.Properties) {
    $Entry = $Font.Value
    if ((Get-FileHash -LiteralPath (Join-Path $FontRoot $Entry.file) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Entry.sha256) { throw "Font checksum mismatch: $($Font.Name)" }
    $License = [IO.File]::ReadAllText((Join-Path $FontRoot $Entry.license_file))
    if ($License -notmatch 'SIL OPEN FONT LICENSE Version 1\.1' -or $License -notmatch 'TERMINATION') { throw "Complete OFL missing: $($Font.Name)" }
}

$Utf8 = [Text.UTF8Encoding]::new($false)
$InventoryPath = Join-Path $OutputRoot "campus-homework-v$Version-files.json"
$Inventory = [ordered]@{name='campus-homework'; version=$Version; code_license='MIT'; font_licenses='Included upstream OFL texts'; files=$Files}
[IO.File]::WriteAllText($InventoryPath, ($Inventory | ConvertTo-Json -Depth 8) + "`n", $Utf8)
if ($ValidateOnly) {
    [PSCustomObject]@{validated=$true; file_count=$Files.Count; bytes=($Files | Measure-Object -Property bytes -Sum).Sum; inventory=$InventoryPath; archive_created=$false} | ConvertTo-Json
    exit 0
}

Add-Type -AssemblyName System.IO.Compression
$ArchivePath = Join-Path $OutputRoot "campus-homework-v$Version.zip"
$Stream = [IO.File]::Open($ArchivePath, [IO.FileMode]::Create)
try {
    $Archive = [IO.Compression.ZipArchive]::new($Stream, [IO.Compression.ZipArchiveMode]::Create, $true, [Text.Encoding]::UTF8)
    try {
        foreach ($File in $Files) {
            $Entry = $Archive.CreateEntry('campus-homework/' + $File.path, [IO.Compression.CompressionLevel]::Optimal)
            $Entry.LastWriteTime = [DateTimeOffset]::new(2026, 1, 1, 0, 0, 0, [TimeSpan]::Zero)
            $Destination = $Entry.Open()
            $Source = [IO.File]::OpenRead((Join-Path $SourceRoot $File.path))
            try { $Source.CopyTo($Destination) } finally { $Source.Dispose(); $Destination.Dispose() }
        }
    } finally { $Archive.Dispose() }
} finally { $Stream.Dispose() }

$Expected = @{}
foreach ($File in $Files) { $Expected['campus-homework/' + $File.path] = $File.sha256 }
$Stream = [IO.File]::OpenRead($ArchivePath)
try {
    $Archive = [IO.Compression.ZipArchive]::new($Stream, [IO.Compression.ZipArchiveMode]::Read)
    try {
        if ($Archive.Entries.Count -ne $Files.Count) { throw 'Archive member count differs from inventory.' }
        foreach ($Entry in $Archive.Entries) {
            if (-not $Expected.ContainsKey($Entry.FullName)) { throw 'Archive contains an unexpected member.' }
            $InputStream = $Entry.Open()
            $Hash = [Security.Cryptography.SHA256]::Create()
            try { $Digest = [BitConverter]::ToString($Hash.ComputeHash($InputStream)).Replace('-', '').ToLowerInvariant() } finally { $InputStream.Dispose(); $Hash.Dispose() }
            if ($Digest -ne $Expected[$Entry.FullName]) { throw "Archive member differs from verified source: $($Entry.FullName)" }
        }
    } finally { $Archive.Dispose() }
} finally { $Stream.Dispose() }
$ArchiveHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
$InventoryHash = (Get-FileHash -LiteralPath $InventoryPath -Algorithm SHA256).Hash.ToLowerInvariant()
$SumsPath = Join-Path $OutputRoot 'SHA256SUMS.txt'
[IO.File]::WriteAllText($SumsPath, "$ArchiveHash  $([IO.Path]::GetFileName($ArchivePath))`n$InventoryHash  $([IO.Path]::GetFileName($InventoryPath))`n", $Utf8)
[PSCustomObject]@{archive=$ArchivePath; sha256=$ArchiveHash; file_count=$Files.Count; inventory=$InventoryPath; checksums=$SumsPath; member_hashes_verified=$true} | ConvertTo-Json
