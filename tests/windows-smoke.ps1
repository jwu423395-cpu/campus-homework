[CmdletBinding()]
param([switch]$RunUiTests, [switch]$Worker, [string]$RunDirectory)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

if ($Worker) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [Windows.Forms.Application]::SetHighDpiMode([Windows.Forms.HighDpiMode]::PerMonitorV2) | Out-Null
    $form = [Windows.Forms.Form]::new()
    $form.Text = 'CampusHomework isolated test target'
    $form.StartPosition = 'Manual'; $form.Location = [Drawing.Point]::new(80,80); $form.Size = [Drawing.Size]::new(760,600)
    $form.BackColor = [Drawing.Color]::AliceBlue
    $label = [Windows.Forms.Label]::new()
    $label.Text = 'Mock question 1: v = ds/dt. This is not a real assignment.'
    $label.Location = [Drawing.Point]::new(30,35); $label.Size = [Drawing.Size]::new(650,45)
    $box = [Windows.Forms.TextBox]::new()
    $box.Name = 'MockAnswer'; $box.AccessibleName = 'Mock answer editor'
    $box.Multiline = $true; $box.AcceptsReturn = $true
    $box.Location = [Drawing.Point]::new(30,160); $box.Size = [Drawing.Size]::new(660,100)
    $upload = [Windows.Forms.Button]::new()
    $upload.Text = 'Open local file'; $upload.Location = [Drawing.Point]::new(30,310); $upload.Size = [Drawing.Size]::new(160,35)
    $upload.add_Click({
        $dialog = [Windows.Forms.OpenFileDialog]::new()
        try { [void]$dialog.ShowDialog($form) } finally { $dialog.Dispose() }
    })
    $form.Controls.AddRange([Windows.Forms.Control[]]@($label,$box,$upload))
    $other = [Windows.Forms.Form]::new()
    $other.Text = 'CampusHomework isolated distractor'
    $other.StartPosition='Manual'; $other.Location=[Drawing.Point]::new(100,100); $other.Size=[Drawing.Size]::new(350,270)
    $other.BackColor=[Drawing.Color]::Red
    $otherBox=[Windows.Forms.TextBox]::new(); $otherBox.Text='KEEP'; $otherBox.Dock='Top'; $other.Controls.Add($otherBox)
    $script:lastSequence = ''
    $timer = [Windows.Forms.Timer]::new(); $timer.Interval=60
    $timer.add_Tick({
        $controlPath=Join-Path $RunDirectory 'control.json'
        if (-not (Test-Path -LiteralPath $controlPath)) { return }
        $command=Get-Content -LiteralPath $controlPath -Raw | ConvertFrom-Json
        if ($command.sequence -eq $script:lastSequence) { return }
        $script:lastSequence=$command.sequence
        switch ($command.action) {
            'focus' { $form.Show(); $form.Activate(); [void]$box.Focus() }
            'blur' { $other.Show(); $other.Activate(); [void]$otherBox.Focus() }
            'move' { $form.Location=[Drawing.Point]::new($form.Left+35,$form.Top+20) }
            'change' { $label.Text='Mock question 2: changed viewport. Do not enter the old answer.'; $label.BackColor=[Drawing.Color]::Yellow }
            'stop' { $other.Close(); $form.Close(); return }
        }
        $point=$box.PointToScreen([Drawing.Point]::new(20,20))
        [ordered]@{ sequence=$command.sequence; hwnd=$form.Handle.ToInt64(); pid=$PID; x=$point.X-$form.Left; y=$point.Y-$form.Top; text=$box.Text; other_text=$otherBox.Text; answer_focused=$box.Focused; selection_start=$box.SelectionStart; selection_length=$box.SelectionLength } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $RunDirectory 'state.json') -Encoding utf8
    })
    $form.add_Shown({ $timer.Start() })
    try { [Windows.Forms.Application]::Run($form) } finally { $timer.Dispose(); $other.Dispose(); $form.Dispose() }
    exit
}

if (-not $RunUiTests) { throw 'Explicit -RunUiTests is required. Reserve the desktop with the single UI operator first; only local test windows are used.' }
if ([Threading.Thread]::CurrentThread.GetApartmentState() -ne 'STA') { throw 'Run with pwsh -NoProfile -STA -File tests/windows-smoke.ps1 -RunUiTests.' }
$adapter = Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts/windows.ps1'
$powerShell = (Get-Process -Id $PID).Path
if (-not $RunDirectory) { $RunDirectory = Join-Path ([IO.Path]::GetTempPath()) ('campus-homework-smoke-' + [Guid]::NewGuid().ToString('N')) }
[void][IO.Directory]::CreateDirectory($RunDirectory)
$workerProcess=$null
$checks=[Collections.Generic.List[object]]::new()

function Command([string]$Name) {
    $sequence=[Guid]::NewGuid().ToString('N')
    $pending=Join-Path $RunDirectory 'control.pending'
    @{sequence=$sequence;action=$Name} | ConvertTo-Json | Set-Content -LiteralPath $pending -Encoding utf8
    Move-Item -LiteralPath $pending -Destination (Join-Path $RunDirectory 'control.json') -Force
    for ($i=0;$i -lt 80;$i++) {
        $path=Join-Path $RunDirectory 'state.json'
        if (Test-Path -LiteralPath $path) {
            try { $state=Get-Content -LiteralPath $path -Raw | ConvertFrom-Json; if ($state.sequence -eq $sequence) { return $state } } catch { }
        }
        Start-Sleep -Milliseconds 50
    }
    throw "Fixture did not acknowledge $Name."
}
function Invoke-Adapter([string[]]$Arguments) {
    $startInfo=[Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName=$powerShell
    $startInfo.UseShellExecute=$false; $startInfo.CreateNoWindow=$true
    $startInfo.RedirectStandardOutput=$true; $startInfo.RedirectStandardError=$true
    $startInfo.StandardOutputEncoding=[Text.UTF8Encoding]::new($false); $startInfo.StandardErrorEncoding=[Text.UTF8Encoding]::new($false)
    foreach ($argument in (@('-NoProfile','-STA','-File',$adapter)+$Arguments)) { $startInfo.ArgumentList.Add($argument) }
    $process=[Diagnostics.Process]::new(); $process.StartInfo=$startInfo
    try {
        [void]$process.Start()
        $stdout=$process.StandardOutput.ReadToEndAsync(); $stderr=$process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(30000)) { $process.Kill($true); throw 'Adapter exceeded the smoke-test timeout.' }
        $output=$stdout.GetAwaiter().GetResult()+$stderr.GetAwaiter().GetResult()
    } finally { $process.Dispose() }
    try { return $output | ConvertFrom-Json } catch { throw "Adapter did not return JSON: $output" }
}
function Observe {
    $script:imageNumber++
    $observation=Invoke-Adapter @('-Action','observe','-WindowHandle',"$($script:fixture.hwnd)",'-ProcessId',"$($script:fixture.pid)",'-Context','mock/task/question-1','-OutputPath',(Join-Path $RunDirectory ("window-$script:imageNumber.png")))
    if (-not $observation.ok) { throw ($observation | ConvertTo-Json -Compress) }
    return $observation
}
function TargetArguments($Observation,[string]$Name) {
    return @('-Action',$Name,'-WindowHandle',"$($script:fixture.hwnd)",'-ProcessId',"$($script:fixture.pid)",'-Context',$Observation.context,'-Token',$Observation.token)
}
function Check([string]$Name,[bool]$Pass,$Evidence) {
    $checks.Add([ordered]@{name=$Name;passed=$Pass;evidence=$Evidence})
    if (-not $Pass) { throw "Smoke check failed: $Name; $($Evidence | ConvertTo-Json -Compress -Depth 6)" }
}

try {
    $script:imageNumber=0
    $workerProcess=Start-Process -FilePath $powerShell -ArgumentList @('-NoProfile','-STA','-File',('"'+$PSCommandPath+'"'),'-Worker','-RunDirectory',('"'+$RunDirectory+'"')) -WindowStyle Hidden -PassThru
    $script:fixture=Command 'focus'
    Start-Sleep -Milliseconds 300
    $o=Observe
    $r=Invoke-Adapter (TargetArguments $o 'focus')
    Check 'fixture explicitly focused with public tool' $r.ok $r
    $o=Observe
    Check 'fixture foreground before first click' $o.foreground @{}
    $r=Invoke-Adapter ((TargetArguments $o 'click')+@('-X',"$($fixture.x)",'-Y',"$($fixture.y)"))
    Check 'local-coordinate click' $r.ok $r
    $o=Observe
    $sample='中文 α 下标 a_n；LaTeX: \frac{1}{2}; literal $x and `backtick`'
    $r=Invoke-Adapter ((TargetArguments $o 'type')+@('-Text',$sample))
    $state=Command 'read'
    Check 'Unicode and literal text input' ($r.ok -and $state.text -ceq $sample) @{result=$r;readback=$state.text}
    $r=Invoke-Adapter ((TargetArguments $o 'type')+@('-Text','MUST_NOT_APPEAR'))
    Check 'single-use token' (-not $r.ok -and $r.code -eq 'stale_observation') $r
    $o=Observe
    $r=Invoke-Adapter ((TargetArguments $o 'click')+@('-X',"$($o.geometry.width)",'-Y','10'))
    Check 'out-of-bounds rejected' (-not $r.ok -and $r.code -eq 'out_of_bounds' -and -not $r.input_may_be_partial) $r
    $o=Observe
    $null=Command 'move'
    $r=Invoke-Adapter ((TargetArguments $o 'type')+@('-Text','MUST_NOT_APPEAR'))
    Check 'moved window invalidates token' (-not $r.ok -and $r.code -eq 'stale_observation') $r
    $o=Observe
    $null=Command 'change'
    $r=Invoke-Adapter ((TargetArguments $o 'type')+@('-Text','MUST_NOT_APPEAR'))
    Check 'changed viewport invalidates token' (-not $r.ok -and $r.code -eq 'stale_observation') $r
    $o=Observe
    $null=Command 'blur'
    $r=Invoke-Adapter ((TargetArguments $o 'type')+@('-Text','MUST_NOT_APPEAR'))
    $state=Command 'read'
    Check 'lost focus stops input' (-not $r.ok -and $r.code -eq 'lost_focus' -and $state.other_text -ceq 'KEEP' -and $state.text -ceq $sample) $r
    $o=Observe
    Add-Type -AssemblyName System.Drawing
    $bitmap=[Drawing.Bitmap]::new($o.screenshot)
    try { $pixel=$bitmap.GetPixel(20,140); $backgroundOnly=($pixel.R -eq 240 -and $pixel.G -eq 248 -and $pixel.B -eq 255) } finally { $bitmap.Dispose() }
    Check 'background target capture excludes red foreground window' $backgroundOnly @{png=$o.screenshot;pixel=@($pixel.R,$pixel.G,$pixel.B)}
    $r=Invoke-Adapter (TargetArguments $o 'focus')
    Check 'explicit focus and lock release after failures' $r.ok $r
    $o=Observe
    $r=Invoke-Adapter ((TargetArguments $o 'key')+@('-Key','Ctrl+A'))
    $state=Command 'read'
    Check 'normal key input' ($r.ok -and $state.text -ceq $sample -and $state.selection_length -eq $sample.Length) @{result=$r;readback=$state.text;answer_focused=$state.answer_focused;selection_length=$state.selection_length}
    Add-Type -AssemblyName System.Windows.Forms
    $before=[Windows.Forms.Clipboard]::GetDataObject()
    $originalFormats=$(if ($before) { @($before.GetFormats($false)) } else { @() })
    if (@($originalFormats | Where-Object { $_ -notin @('UnicodeText','Text','OEMText','Locale','System.String') }).Count -gt 0) {
        $checks.Add([ordered]@{name='selected plain text and clipboard restoration';passed=$null;skipped=$true;reason='Original rich/image/file clipboard left untouched.'})
    } else {
      $originalText=$(if ($originalFormats.Count) { [Windows.Forms.Clipboard]::GetText() } else { '' })
      $clipboardBackup=Join-Path $RunDirectory 'original-clipboard.private.json'
      [IO.File]::WriteAllText($clipboardBackup,'',[Text.UTF8Encoding]::new($false))
      $security=[Security.AccessControl.FileSecurity]::new()
      $security.SetAccessRuleProtection($true,$false)
      $security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.WindowsIdentity]::GetCurrent().User,'FullControl','Allow'))
      Set-Acl -LiteralPath $clipboardBackup -AclObject $security
      [IO.File]::WriteAllText($clipboardBackup,(@{text=[string]$originalText}|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
      try {
        [Windows.Forms.Clipboard]::SetText('clipboard preservation sentinel')
        $o=Observe
        $r=Invoke-Adapter (TargetArguments $o 'clipboard-text')
        if (-not $r.ok -and $r.code -eq 'stale_observation' -and -not $r.input_may_be_partial) {
            $state=Command 'read'
            Check 'clipboard stale observation safely refused before one re-observation' ($state.answer_focused -and $state.text -ceq $sample -and $state.selection_length -eq $sample.Length -and [Windows.Forms.Clipboard]::GetText() -ceq 'clipboard preservation sentinel') $r
            Start-Sleep -Milliseconds 350
            $o=Observe
            $r=Invoke-Adapter (TargetArguments $o 'clipboard-text')
        }
        $restored=[Windows.Forms.Clipboard]::GetText()
        Check 'selected plain text and clipboard restoration' ($r.ok -and $r.text -ceq $sample -and $restored -ceq 'clipboard preservation sentinel') @{result=$r;clipboard_restored=($restored -ceq 'clipboard preservation sentinel')}
      } finally {
        $originalRestored=$false
        for ($attempt=0;$attempt -lt 5;$attempt++) {
          try {
            if ([string]::IsNullOrEmpty($originalText)) { [Windows.Forms.Clipboard]::Clear() } else { [Windows.Forms.Clipboard]::SetText([string]$originalText) }
            if ([Windows.Forms.Clipboard]::GetText() -cne $originalText) { throw 'Clipboard restore readback mismatch.' }
            $originalRestored=$true; break
          } catch { Start-Sleep -Milliseconds 100 }
        }
        if (-not $originalRestored) { throw "Test clipboard could not be restored; original retained privately at $clipboardBackup" }
        Remove-Item -LiteralPath $clipboardBackup -Force
      }
    }
    $o=Observe
    $r=Invoke-Adapter ((TargetArguments $o 'automation')+@('-Limit','40'))
    Check 'UIAutomation semantic enumeration' ($r.ok -and @($r.automation.elements | Where-Object name -eq 'Mock answer editor').Count -eq 1) @{result=$r}
    $lockPath=Join-Path ([IO.Path]::GetTempPath()) 'campus-homework-windows/operator.lock'
    $held=[IO.File]::Open($lockPath,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    try { $r=Invoke-Adapter @('-Action','list','-ProcessName','campus_homework_no_such_process') } finally { $held.Dispose() }
    Check 'exclusive OS lock' (-not $r.ok -and $r.code -eq 'ui_busy') $r
    $r=Invoke-Adapter @('-Action','list','-ProcessName','campus_homework_no_such_process')
    Check 'OS lock released' $r.ok $r
    $passedChecks=@($checks | Where-Object { $_.passed -eq $true }).Count
    $skippedChecks=@($checks | Where-Object { $_.skipped -eq $true }).Count
    [ordered]@{ok=$true;passed=$passedChecks;skipped=$skippedChecks;scope='Independent local WinForms only; no real platform or browser';checks=$checks.ToArray();run_directory=$RunDirectory} | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $RunDirectory 'result.json') -Encoding utf8
    [ordered]@{ok=$true;passed=$passedChecks;skipped=$skippedChecks;result=(Join-Path $RunDirectory 'result.json')} | ConvertTo-Json -Compress
} catch {
    [ordered]@{ok=$false;message=$_.Exception.Message;checks=$checks.ToArray();run_directory=$RunDirectory} | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $RunDirectory 'result.json') -Encoding utf8
    [ordered]@{ok=$false;message=$_.Exception.Message;result=(Join-Path $RunDirectory 'result.json')} | ConvertTo-Json -Compress
    exit 1
} finally {
    if ($workerProcess -and -not $workerProcess.HasExited) {
        try { $null=Command 'stop' } catch { }
        if (-not $workerProcess.HasExited) { Stop-Process -Id $workerProcess.Id -ErrorAction SilentlyContinue }
    }
}
