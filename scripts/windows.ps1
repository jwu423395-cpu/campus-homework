# Windows desktop adapter. All coordinates are physical pixels in the observed window.
# Run with pwsh -NoProfile -STA -File windows.ps1 ... . No platform API or credentials used.
[CmdletBinding()]
param(
    [ValidateSet('list','launch','restore','observe','focus','click','key','type','clipboard-text','automation','upload-file')][string]$Action,
    [long]$WindowHandle = 0,
    [uint32]$ProcessId = 0,
    [string]$Token,
    [string]$Context,
    [string]$OutputPath,
    [int]$X = -1,
    [int]$Y = -1,
    [ValidateSet('left','right')][string]$Button = 'left',
    [string]$Key,
    [AllowEmptyString()][string]$Text,
    [string]$FilePath,
    [string]$Executable,
    [switch]$Web,
    [string]$ProcessName,
    [ValidateRange(1,500)][int]$Limit = 100,
    [switch]$Help,
    [Parameter(DontShow)][string]$WorkerRequest
)

$ErrorActionPreference = 'Stop'
$utf8Output = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8Output
$OutputEncoding = $utf8Output
$workerStatusPath = $null
$workerRecoveryPath = $null
if ($WorkerRequest) {
    try {
        $workerEnvelope = Get-Content -LiteralPath $WorkerRequest -Raw | ConvertFrom-Json
        if ($workerEnvelope.version -ne 1) { throw 'Invalid protocol version.' }
        $allowedWorkerParameters = @('Action','WindowHandle','ProcessId','Token','Context','OutputPath','X','Y','Button','Key','Text','FilePath','Executable','Web','ProcessName','Limit')
        foreach ($entry in $workerEnvelope.parameters.PSObject.Properties) {
            if ($entry.Name -notin $allowedWorkerParameters) { throw 'Invalid worker parameter.' }
            Set-Variable -Name $entry.Name -Value $entry.Value -Scope Script
            $PSBoundParameters[$entry.Name] = $entry.Value
        }
        $workerStatusPath = [string]$workerEnvelope.status_path
        $workerRecoveryPath = [string]$workerEnvelope.recovery_path
    } catch {
        @{ok=$false;code='invalid_worker_request';message='Worker parameters could not be loaded; their contents are not included.'} | ConvertTo-Json -Compress
        exit 1
    }
}

if ($Help -or -not $Action) {
    [ordered]@{
        tool='campus-homework Windows desktop adapter'; invocation='pwsh -NoProfile -STA -File scripts/windows.ps1'
        actions=@('list','launch','restore','observe','focus','click','key','type','clipboard-text','automation','upload-file')
        observe='-Action observe -WindowHandle HWND -ProcessId PID -Context course/task/question -OutputPath ABSOLUTE.png'
        input='-Action click|key|type -WindowHandle HWND -ProcessId PID -Context SAME_CONTEXT -Token FRESH_TOKEN'
        arguments=@('click: -X LOCAL_PIXEL_X -Y LOCAL_PIXEL_Y','key: -Key Ctrl+A|Tab|Enter|PageDown','type: -Text TEXT','upload-file: -FilePath ABSOLUTE_FILE')
        rules=@('Read each screenshot before input.','Every input consumes its observation token.','HWND, PID and operator-checked context are required.','This tool does not understand questions or verify submission.')
        deadlines='Capture/UIAutomation phases: 15 seconds. Other calls: 15 seconds; type scales with text length, maximum 90 seconds.'
        recovery='Clipboard restoration failure returns clipboard_restored=false and a private local recovery_path; original text is never included in the error.'
        reference='references/windows.md'
    } | ConvertTo-Json -Depth 4
    return
}

$ErrorActionPreference = 'Stop'
$inputMayBePartial = $false
$adapterLock = $null
$dpiScope = [IntPtr]::Zero
$stateRoot = Join-Path ([System.IO.Path]::GetTempPath()) 'campus-homework-windows'
$clipboardRecoveryPath = $null
$clipboardRestored = $null
$observationDifference = $null

function Fail([string]$Code, [string]$Message) {
    $failure = [System.InvalidOperationException]::new($Message)
    $failure.Data['code'] = $Code
    throw $failure
}

function Write-PrivateRuntimeFile([string]$Path, [string]$Contents) {
    # Restrict access before writing parameters or clipboard contents.
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $stream.Dispose()
    try {
        $security = [Security.AccessControl.FileSecurity]::new()
        $security.SetAccessRuleProtection($true, $false)
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($identity, 'FullControl', 'Allow'))
        Set-Acl -LiteralPath $Path -AclObject $security
        [IO.File]::WriteAllText($Path, $Contents, [Text.UTF8Encoding]::new($false))
    } catch {
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Set-WorkerPhase([string]$Name) {
    if ($workerStatusPath) {
        $phase = @{phase=$Name;sequence=[Guid]::NewGuid().ToString('N');input_may_be_partial=$inputMayBePartial}
        [IO.File]::WriteAllText($workerStatusPath, ($phase | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
    }
}

function Invoke-WindowCapture([string]$Path) {
    Set-WorkerPhase 'capture'
    try { [CampusHomework.DesktopV1]::Capture($WindowHandle, $ProcessId, $Path) }
    finally { Set-WorkerPhase 'ready' }
}

function Initialize-Native {
    if ('CampusHomework.DesktopV1' -as [type]) { return }
    Add-Type -AssemblyName System.Drawing
    Add-Type -AssemblyName System.Windows.Forms
    # Newer PowerShell/.NET releases split GDI types into private Windows assemblies.
    $nativeReferences = @('System.Drawing.Common','System.Drawing.Primitives','System.Threading.Thread')
    $nativeReferences += @(Get-ChildItem -LiteralPath $PSHOME -Filter 'System.Private.Windows*.dll' | ForEach-Object FullName)
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
namespace CampusHomework {
 public static class DesktopV1 {
  public delegate bool EnumCallback(IntPtr handle, IntPtr extra);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left,Top,Right,Bottom; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X,Y; public POINT(int x,int y){X=x;Y=y;} }
  [StructLayout(LayoutKind.Sequential)] struct MSG { public IntPtr window; public uint message; public UIntPtr wParam; public IntPtr lParam; public uint time; public POINT point; public uint extra; }
  [StructLayout(LayoutKind.Sequential)] struct GUITHREADINFO {
   public uint size,flags; public IntPtr active,focus,capture,menuOwner,moveSize,caret; public RECT caretRect;
  }
  [StructLayout(LayoutKind.Sequential)] struct INPUT { public uint type; public UNION data; }
  [StructLayout(LayoutKind.Explicit)] struct UNION {
   [FieldOffset(0)] public KEYBOARD keyboard; [FieldOffset(0)] public MOUSE mouse;
  }
  [StructLayout(LayoutKind.Sequential)] struct KEYBOARD { public ushort vk,scan; public uint flags,time; public UIntPtr extra; }
  [StructLayout(LayoutKind.Sequential)] struct MOUSE { public int x,y; public uint data,flags,time; public UIntPtr extra; }
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumCallback callback,IntPtr extra);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr handle);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr handle);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr handle);
  [DllImport("user32.dll")] public static extern bool IsHungAppWindow(IntPtr handle);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr handle,out uint id);
  [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr handle,StringBuilder text,int count);
  [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr handle,StringBuilder text,int count);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr handle,out RECT rect);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetClipboardSequenceNumber();
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr handle,uint flag);
  [DllImport("user32.dll")] static extern bool GetGUIThreadInfo(uint thread,ref GUITHREADINFO info);
  [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr handle);
  [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr handle);
  [DllImport("user32.dll",SetLastError=true)] static extern bool AttachThreadInput(uint first,uint second,bool attach);
  [DllImport("user32.dll")] static extern bool PeekMessage(out MSG message,IntPtr window,uint first,uint last,uint remove);
  [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr handle,int command);
  [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
  [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr handle);
  [DllImport("user32.dll",SetLastError=true)] static extern bool PrintWindow(IntPtr handle,IntPtr hdc,uint flags);
  [DllImport("user32.dll")] static extern bool SetCursorPos(int x,int y);
  [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(POINT point);
  [DllImport("user32.dll",SetLastError=true)] static extern uint SendInput(uint count,INPUT[] input,int size);
  public static string Title(long handle) { var b=new StringBuilder(1024); GetWindowText(new IntPtr(handle),b,b.Capacity); return b.ToString(); }
  public static string ClassName(long handle) { var b=new StringBuilder(256); GetClassName(new IntPtr(handle),b,b.Capacity); return b.ToString(); }
  public static bool Foreground(long handle) { return GetAncestor(GetForegroundWindow(),2)==new IntPtr(handle); }
  public static void Check(long handle,uint id,bool foreground) {
   uint actual; var h=new IntPtr(handle); GetWindowThreadProcessId(h,out actual);
   if(id==0 || !IsWindow(h) || actual!=id) throw new InvalidOperationException("target_changed: HWND or PID no longer matches.");
   if(foreground && !Foreground(handle)) {
    var active=GetAncestor(GetForegroundWindow(),2); uint activeId;
    GetWindowThreadProcessId(active,out activeId);
    throw new InvalidOperationException("lost_focus: Target window is not foreground; input stopped. actual_foreground_hwnd="+active.ToInt64()+", actual_foreground_pid="+activeId);
   }
  }
  public static long FocusedControl(long handle) {
   uint owner; uint thread=GetWindowThreadProcessId(new IntPtr(handle),out owner);
   var info=new GUITHREADINFO(); info.size=(uint)Marshal.SizeOf(typeof(GUITHREADINFO));
   return GetGUIThreadInfo(thread,ref info)?info.focus.ToInt64():0;
  }
  public static void Focus(long handle,uint id) {
   Check(handle,id,false); var h=new IntPtr(handle);
   if(IsIconic(h)) ShowWindow(h,9);
   if(Foreground(handle)) return;
   uint ignored;
   uint currentThread=GetCurrentThreadId();
   uint foregroundThread=GetWindowThreadProcessId(GetForegroundWindow(),out ignored);
   uint targetThread=GetWindowThreadProcessId(h,out ignored);
   MSG message; PeekMessage(out message,IntPtr.Zero,0,0,0);
   bool attachedForeground=false,attachedTarget=false;
   // Only an explicitly requested focus/restore may activate a background target.
   // Input actions still require foreground and never call this method implicitly.
   try {
    if(foregroundThread!=0 && foregroundThread!=currentThread)
     attachedForeground=AttachThreadInput(currentThread,foregroundThread,true);
    if(targetThread!=currentThread && targetThread!=foregroundThread)
     attachedTarget=AttachThreadInput(currentThread,targetThread,true);
    BringWindowToTop(h);
    SetForegroundWindow(h);
   } finally {
    if(attachedTarget) AttachThreadInput(currentThread,targetThread,false);
    if(attachedForeground) AttachThreadInput(currentThread,foregroundThread,false);
   }
   Thread.Sleep(150);
   Check(handle,id,true);
  }
  public static void Capture(long handle,uint id,string path) {
   Check(handle,id,false); var h=new IntPtr(handle);
   if(IsIconic(h) || !IsWindowVisible(h) || IsHungAppWindow(h)) throw new InvalidOperationException("capture_unavailable: Window is minimized, hidden or unresponsive.");
   RECT r; if(!GetWindowRect(h,out r) || r.Right<=r.Left || r.Bottom<=r.Top) throw new InvalidOperationException("capture_unavailable: Invalid geometry.");
   // GDI controls may leave alpha bytes unset. RGB avoids disappearing text in PNGs.
   using(var bitmap=new Bitmap(r.Right-r.Left,r.Bottom-r.Top,PixelFormat.Format32bppRgb)) {
    using(var g=Graphics.FromImage(bitmap)) {
     g.Clear(Color.Magenta); var dc=g.GetHdc(); bool printed;
     try { printed=PrintWindow(h,dc,2); } finally {g.ReleaseHdc(dc);}
     if(!printed) throw new InvalidOperationException("capture_unavailable: PrintWindow failed; no desktop screenshot fallback.");
    }
    // Reject obviously unsupported/blank captures instead of accidentally trusting a black CEF frame.
    int same=0,total=0; Color first=bitmap.GetPixel(0,0);
    for(int y=0;y<bitmap.Height;y+=Math.Max(1,bitmap.Height/40)) for(int x=0;x<bitmap.Width;x+=Math.Max(1,bitmap.Width/40)) {
     var c=bitmap.GetPixel(x,y); total++; if(Math.Abs(c.R-first.R)+Math.Abs(c.G-first.G)+Math.Abs(c.B-first.B)<12) same++;
    }
    if(total>0 && same>total*0.995) throw new InvalidOperationException("capture_unavailable: Capture is nearly uniform; inspect with a supported window-capture tool.");
    bitmap.Save(path,ImageFormat.Png);
   }
   Check(handle,id,false);
  }
  public static double ChangedFraction(string original,string current) {
   using(var a=new Bitmap(original)) using(var b=new Bitmap(current)) {
    if(a.Size!=b.Size) return 1;
    // A small tolerance ignores caret blinking/antialiasing, not scrolling or layout changes.
    long changed=0,total=0;
    for(int y=0;y<a.Height;y+=2) for(int x=0;x<a.Width;x+=2) {
     Color p=a.GetPixel(x,y),q=b.GetPixel(x,y); total++;
     if(Math.Max(Math.Abs(p.R-q.R),Math.Max(Math.Abs(p.G-q.G),Math.Abs(p.B-q.B)))>32) changed++;
    }
    return total==0?1:(double)changed/total;
   }
  }
  static void Send(INPUT[] values) {
   if(SendInput((uint)values.Length,values,Marshal.SizeOf(typeof(INPUT)))!=values.Length)
    throw new InvalidOperationException("input_failed: Native input failed; inspect before retrying.");
  }
  static void VirtualKey(ushort key,bool up) {
   var v=new INPUT[1];v[0].type=1;v[0].data.keyboard.vk=key;v[0].data.keyboard.flags=up?2u:0u;Send(v);
  }
  public static void Keys(long handle,uint id,ushort[] keys) {
   Check(handle,id,true); int down=0;
   try { foreach(ushort key in keys) { Check(handle,id,true);VirtualKey(key,false);down++; } }
   finally { for(int i=down-1;i>=0;i--) VirtualKey(keys[i],true); }
  }
  public static void Type(long handle,uint id,string text) {
   Check(handle,id,true);
   foreach(char c in text) {
    Check(handle,id,true);if(c=='\r') continue;
    if(c=='\n') { Keys(handle,id,new ushort[]{13}); continue; }
    var v=new INPUT[2];v[0].type=1;v[1].type=1;
    v[0].data.keyboard.scan=c;v[1].data.keyboard.scan=c;v[0].data.keyboard.flags=4;v[1].data.keyboard.flags=6;
    Send(v);Thread.Sleep(10);
   }
   Check(handle,id,true);
  }
  public static void Click(long handle,uint id,int x,int y,bool right) {
   Check(handle,id,true);RECT r;GetWindowRect(new IntPtr(handle),out r);
   if(x<0||y<0||x>=r.Right-r.Left||y>=r.Bottom-r.Top) throw new InvalidOperationException("out_of_bounds: Point must be inside the observed window.");
   var point=new POINT(r.Left+x,r.Top+y);
   if(GetAncestor(WindowFromPoint(point),2)!=new IntPtr(handle)) throw new InvalidOperationException("occluded_target: Another window covers the target point.");
   SetCursorPos(point.X,point.Y);Check(handle,id,true);
   var v=new INPUT[2];v[0].data.mouse.flags=right?8u:2u;v[1].data.mouse.flags=right?16u:4u;Send(v);
  }
 }
}
'@ -ReferencedAssemblies $nativeReferences
}

function Get-TargetGeometry {
    [CampusHomework.DesktopV1]::Check($WindowHandle, $ProcessId, $false)
    $rectangle = [CampusHomework.DesktopV1+RECT]::new()
    if (-not [CampusHomework.DesktopV1]::GetWindowRect([IntPtr]$WindowHandle, [ref]$rectangle)) { Fail 'target_changed' 'Cannot read target geometry.' }
    [ordered]@{
        left = $rectangle.Left; top = $rectangle.Top
        width = $rectangle.Right - $rectangle.Left; height = $rectangle.Bottom - $rectangle.Top
        dpi = [CampusHomework.DesktopV1]::GetDpiForWindow([IntPtr]$WindowHandle)
    }
}

function Get-TargetIdentity {
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    [ordered]@{
        hwnd = $WindowHandle; pid = $ProcessId; process_start_ticks = $process.StartTime.ToUniversalTime().Ticks
        title = [CampusHomework.DesktopV1]::Title($WindowHandle)
        class_name = [CampusHomework.DesktopV1]::ClassName($WindowHandle)
    }
}

function Get-VisibleWindows {
    $rows = [System.Collections.Generic.List[object]]::new()
    $callback = [CampusHomework.DesktopV1+EnumCallback]{ param($handle, $extra)
        if ([CampusHomework.DesktopV1]::IsWindowVisible($handle)) {
            [uint32]$owner = 0
            [void][CampusHomework.DesktopV1]::GetWindowThreadProcessId($handle, [ref]$owner)
            $process = Get-Process -Id $owner -ErrorAction SilentlyContinue
            $title = [CampusHomework.DesktopV1]::Title($handle.ToInt64())
            if ($process -and $title -and (-not $ProcessName -or $process.ProcessName -eq $ProcessName)) {
                $rows.Add([ordered]@{ hwnd=$handle.ToInt64(); pid=$owner; process_name=$process.ProcessName; title=$title })
            }
        }
        return $true
    }
    [void][CampusHomework.DesktopV1]::EnumWindows($callback, [IntPtr]::Zero)
    return $rows.ToArray()
}

function Require-Target {
    if ($WindowHandle -eq 0 -or $ProcessId -eq 0) { Fail 'missing_target' 'Pass both -WindowHandle and -ProcessId from list or launch.' }
}

function Require-Context {
    if ([string]::IsNullOrWhiteSpace($Context)) { Fail 'missing_context' 'Pass -Context, an operator-verified course/task/question or navigation label.' }
}

function Read-Observation([bool]$RequireForeground) {
    Require-Target
    Require-Context
    if ($Token -notmatch '^[a-f0-9]{32}$') { Fail 'invalid_token' 'Pass the token from a fresh observe result.' }
    $recordPath = Join-Path $stateRoot ($Token + '.json')
    if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) { Fail 'stale_observation' 'Token is missing, consumed or expired. Observe again.' }
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    if ($record.generation -ne (Get-InputGeneration)) { Fail 'stale_observation' 'Another input occurred after observe. Observe again.' }
    if ($record.target.hwnd -ne $WindowHandle -or $record.target.pid -ne $ProcessId -or $record.context -cne $Context) { Fail 'context_mismatch' 'HWND, PID or operator context differs from the observation.' }
    if (([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - $record.created_ms) -gt 120000) { Fail 'stale_observation' 'Observation is older than 120 seconds. Observe again.' }
    [CampusHomework.DesktopV1]::Check($WindowHandle, $ProcessId, $RequireForeground)
    $identity = Get-TargetIdentity
    if ($identity.process_start_ticks -ne $record.target.process_start_ticks -or $identity.title -cne $record.target.title) { Fail 'stale_observation' 'Process identity or window title changed. Observe again.' }
    $geometry = Get-TargetGeometry
    foreach ($field in @('left','top','width','height','dpi')) {
        if ($geometry[$field] -ne $record.geometry.$field) { Fail 'stale_observation' 'Window geometry or DPI changed. Observe again.' }
    }
    $checkPath = Join-Path $stateRoot ($Token + '.check.png')
    $retainCheck = $false
    try {
        Invoke-WindowCapture $checkPath
        $changed = [CampusHomework.DesktopV1]::ChangedFraction($record.snapshot, $checkPath)
        if ($changed -gt 0.002) {
            $security = [Security.AccessControl.FileSecurity]::new()
            $security.SetAccessRuleProtection($true, $false)
            $security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.WindowsIdentity]::GetCurrent().User, 'FullControl', 'Allow'))
            Set-Acl -LiteralPath $checkPath -AclObject $security
            $retainCheck = $true
            $script:observationDifference = @{ changed_fraction=$changed; threshold=0.002; observation_screenshot=$record.snapshot; current_screenshot=$checkPath }
            Fail 'stale_observation' 'Window contents changed since observe. Wait for a stable page and observe again.'
        }
    } finally {
        if (-not $retainCheck) { Remove-Item -LiteralPath $checkPath -Force -ErrorAction SilentlyContinue }
    }
    return $record
}

function Consume-Observation($Record) {
    ((Get-InputGeneration) + 1) | Set-Content -LiteralPath (Join-Path $stateRoot 'generation.txt') -Encoding ascii
    Remove-Item -LiteralPath (Join-Path $stateRoot ($Token + '.json')) -Force
    Remove-Item -LiteralPath $Record.snapshot -Force -ErrorAction SilentlyContinue
}

function Get-InputGeneration {
    $generationPath = Join-Path $stateRoot 'generation.txt'
    if (Test-Path -LiteralPath $generationPath -PathType Leaf) { return [long](Get-Content -LiteralPath $generationPath -Raw) }
    return [long]0
}

function Get-KeyCodes([string]$Description) {
    if ([string]::IsNullOrWhiteSpace($Description)) { Fail 'invalid_key' 'Pass -Key, for example Tab, Ctrl+A, Backspace, Enter or PageDown.' }
    $names = @{
        CTRL=17; CONTROL=17; SHIFT=16; ALT=18; ENTER=13; RETURN=13; TAB=9; ESC=27; ESCAPE=27
        BACKSPACE=8; DELETE=46; HOME=36; END=35; LEFT=37; UP=38; RIGHT=39; DOWN=40
        PAGEUP=33; PAGEDOWN=34; SPACE=32
    }
    $values = [System.Collections.Generic.List[ushort]]::new()
    foreach ($part in $Description.ToUpperInvariant().Split('+')) {
        if ($names.ContainsKey($part)) { $values.Add([ushort]$names[$part]) }
        elseif ($part -match '^[A-Z0-9]$') { $values.Add([ushort][char]$part) }
        else { Fail 'invalid_key' "Unsupported key name: $part" }
    }
    return ,$values.ToArray()
}

function Initialize-Automation {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
}

function Read-Automation {
    Set-WorkerPhase 'automation'
    try {
    Initialize-Automation
    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
    if ($null -eq $root) { Fail 'automation_unavailable' 'No accessible UIAutomation root. Use the latest target-window screenshot.' }
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $queue = [System.Collections.Generic.Queue[object]]::new()
    $queue.Enqueue($root)
    $rows = [System.Collections.Generic.List[object]]::new()
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while ($queue.Count -gt 0 -and $rows.Count -lt $Limit -and $watch.Elapsed.TotalSeconds -lt 3) {
        $element = $queue.Dequeue()
        $current = $element.Current
        # Deliberately do not read ValuePattern, document HTML, passwords, cookies or tokens.
        $rows.Add([ordered]@{
            name = $(if ($current.IsPassword) { '[password field]' } else { $current.Name })
            automation_id = $current.AutomationId; control_type = $current.ControlType.ProgrammaticName
            enabled = $current.IsEnabled; offscreen = $current.IsOffscreen
            bounds = [ordered]@{ left=$current.BoundingRectangle.Left; top=$current.BoundingRectangle.Top; width=$current.BoundingRectangle.Width; height=$current.BoundingRectangle.Height }
        })
        $child = $walker.GetFirstChild($element)
        while ($child -and $queue.Count -lt $Limit * 2) { $queue.Enqueue($child); $child = $walker.GetNextSibling($child) }
    }
    return [ordered]@{ elements=$rows.ToArray(); truncated=($queue.Count -gt 0); values_read=$false }
    } finally { Set-WorkerPhase 'ready' }
}

function Copy-SelectedText {
    if ([Threading.Thread]::CurrentThread.GetApartmentState() -ne 'STA') { Fail 'sta_required' 'Clipboard operations require pwsh -STA.' }
    $original = [Windows.Forms.Clipboard]::GetDataObject()
    $formats = $(if ($original) { @($original.GetFormats($false)) } else { @() })
    $hadData = $formats.Count -gt 0
    # Rich/OLE clipboard data can contain delayed native handles that cannot be safely cloned.
    # Refuse before Ctrl+C rather than risk destroying it, and never inspect or return HTML.
    $plainFormats = @('UnicodeText','Text','OEMText','Locale','System.String')
    if (@($formats | Where-Object { $_ -notin $plainFormats }).Count -gt 0) { Fail 'clipboard_backup_unsupported' 'Clipboard contains non-plain-text data. It was left untouched; use screenshot or accessible visible text instead.' }
    $savedText = ''
    if ($hadData) {
        foreach ($format in @([Windows.Forms.TextDataFormat]::UnicodeText, [Windows.Forms.TextDataFormat]::Text)) {
            if ([Windows.Forms.Clipboard]::ContainsText($format)) { $savedText=[Windows.Forms.Clipboard]::GetText($format); break }
        }
    }
    $backupPath = $workerRecoveryPath
    if (-not $backupPath) { $backupPath=Join-Path $stateRoot ([Guid]::NewGuid().ToString('N') + '.clipboard-recovery.json') }
    $backup = @{format='UnicodeText';had_data=$hadData;text=[string]$savedText;created_utc=[DateTime]::UtcNow.ToString('o')}
    Write-PrivateRuntimeFile $backupPath ($backup | ConvertTo-Json -Compress)
    $script:clipboardRecoveryPath=$backupPath
    $script:clipboardRestored=$false
    $sequenceBefore = [CampusHomework.DesktopV1]::GetClipboardSequenceNumber()
    $copiedText = $null
    try {
        # Do not clear first: a page that refuses copying should leave existing text intact.
        [CampusHomework.DesktopV1]::Keys($WindowHandle, $ProcessId, [ushort[]]@(17,67))
        for ($attempt=0; $attempt -lt 20; $attempt++) {
            [CampusHomework.DesktopV1]::Check($WindowHandle, $ProcessId, $true)
            if ([CampusHomework.DesktopV1]::GetClipboardSequenceNumber() -ne $sequenceBefore -and [Windows.Forms.Clipboard]::ContainsText([Windows.Forms.TextDataFormat]::UnicodeText)) {
                $copiedText=[Windows.Forms.Clipboard]::GetText([Windows.Forms.TextDataFormat]::UnicodeText)
                break
            }
            Start-Sleep -Milliseconds 50
        }
        if ($null -eq $copiedText) { Fail 'copy_unavailable' 'Selection did not produce new plain text. No HTML is returned; inspect the page.' }
    } finally {
        for ($restoreAttempt=0; $restoreAttempt -lt 5; $restoreAttempt++) {
            try {
                if ($hadData -and -not [string]::IsNullOrEmpty($savedText)) {
                    [Windows.Forms.Clipboard]::SetText([string]$savedText, [Windows.Forms.TextDataFormat]::UnicodeText)
                    if ([Windows.Forms.Clipboard]::GetText([Windows.Forms.TextDataFormat]::UnicodeText) -cne $savedText) { throw 'Clipboard readback mismatch.' }
                } else {
                    [Windows.Forms.Clipboard]::Clear()
                    if ([Windows.Forms.Clipboard]::ContainsText()) { throw 'Clipboard was not cleared.' }
                }
                $script:clipboardRestored=$true
                break
            } catch { Start-Sleep -Milliseconds 100 }
        }
        if ($clipboardRestored) {
            Remove-Item -LiteralPath $backupPath -Force
            $script:clipboardRecoveryPath=$null
        } else {
            Fail 'clipboard_restore_failed' 'Original plain text remains in the private local recovery_path. Restore it before continuing clipboard operations.'
        }
    }
    return $copiedText
}

function Invoke-BoundedWorker($Parameters) {
    $process = $null
    $requestPath = $null
    $statusPath = $null
    $recoveryPath = $null
    $lastPhase = @{phase='startup';sequence='startup';input_may_be_partial=$false}
    $exitCode = 1
    try {
        [void][IO.Directory]::CreateDirectory($stateRoot)
        $requestId = [Guid]::NewGuid().ToString('N')
        $requestPath = Join-Path $stateRoot ($requestId + '.request.json')
        $statusPath = Join-Path $stateRoot ($requestId + '.status.json')
        $recoveryPath = Join-Path $stateRoot ($requestId + '.clipboard-recovery.json')
        $forwarded = @{}
        $publicParameters = @('Action','WindowHandle','ProcessId','Token','Context','OutputPath','X','Y','Button','Key','Text','FilePath','Executable','Web','ProcessName','Limit')
        foreach ($parameterName in $Parameters.Keys) {
            if ($parameterName -in $publicParameters) {
                $value = $Parameters[$parameterName]
                if ($value -is [Management.Automation.SwitchParameter]) { $value=$value.IsPresent }
                $forwarded[$parameterName]=$value
            }
        }
        $seconds = 15
        if ($Action -eq 'type') {
            if ($Text.Length -gt 7500) {
                return @{exit_code=1;output=(@{ok=$false;action=$Action;code='text_too_long';message='At most 7500 UTF-16 characters per observed input. Split the answer and observe each insertion point again.';input_may_be_partial=$false} | ConvertTo-Json -Compress)}
            }
            $seconds=[Math]::Min(90,[Math]::Max(15,12+[Math]::Ceiling($Text.Length*0.012)))
        }
        Write-PrivateRuntimeFile $statusPath ($lastPhase | ConvertTo-Json -Compress)
        $envelope=@{version=1;parameters=$forwarded;status_path=$statusPath;recovery_path=$recoveryPath}
        Write-PrivateRuntimeFile $requestPath ($envelope | ConvertTo-Json -Depth 6 -Compress)
        $start=[Diagnostics.ProcessStartInfo]::new()
        $start.FileName=(Get-Process -Id $PID).Path
        $start.UseShellExecute=$false
        $start.CreateNoWindow=$true
        $start.RedirectStandardOutput=$true
        $start.RedirectStandardError=$true
        $start.StandardOutputEncoding=$utf8Output
        $start.StandardErrorEncoding=$utf8Output
        foreach ($argument in @('-NoProfile','-STA','-File',$PSCommandPath,'-WorkerRequest',$requestPath)) { $start.ArgumentList.Add($argument) }
        $process=[Diagnostics.Process]::new()
        $process.StartInfo=$start
        [void]$process.Start()
        $stdout=$process.StandardOutput.ReadToEndAsync()
        $stderr=$process.StandardError.ReadToEndAsync()
        $overall=[Diagnostics.Stopwatch]::StartNew()
        $phaseWatch=[Diagnostics.Stopwatch]::StartNew()
        $expired=$false
        $timeoutSeconds=$seconds
        while (-not $process.WaitForExit(100)) {
            try {
                $currentPhase=Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
                if ($currentPhase.sequence -ne $lastPhase.sequence) { $lastPhase=$currentPhase; $phaseWatch.Restart() }
            } catch { } # A writer may be replacing a short status record; retain the previous deadline.
            $phaseSeconds=$(if ($lastPhase.phase -eq 'input' -and $Action -eq 'type') { $seconds } else { 15 })
            if ($overall.Elapsed.TotalSeconds -ge $seconds -or $phaseWatch.Elapsed.TotalSeconds -ge $phaseSeconds) {
                $expired=$true
                if ($phaseWatch.Elapsed.TotalSeconds -ge $phaseSeconds -and $overall.Elapsed.TotalSeconds -lt $seconds) { $timeoutSeconds=$phaseSeconds }
                $process.Kill($true)
                [void]$process.WaitForExit(3000)
                break
            }
        }
        if ($expired) {
            $errorResult=[ordered]@{ok=$false;action=$Action;code='worker_timeout';message='The UI worker exceeded its deadline and was terminated. Inspect the current target before retrying.';phase=$lastPhase.phase;timeout_seconds=$timeoutSeconds;overall_deadline_seconds=$seconds;input_may_be_partial=[bool]$lastPhase.input_may_be_partial}
        } else {
            $streamBudget=[Math]::Max(1,[int](($seconds-$overall.Elapsed.TotalSeconds)*1000))
            if (-not [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($stdout,$stderr),$streamBudget)) {
                $streamFailure=[ordered]@{ok=$false;action=$Action;code='worker_output_timeout';message='Worker output did not close before the deadline; raw diagnostics and parameter contents are omitted.';input_may_be_partial=[bool]$lastPhase.input_may_be_partial}
                if ($Action -eq 'clipboard-text' -and (Test-Path -LiteralPath $recoveryPath -PathType Leaf)) { $streamFailure.clipboard_restored=$false; $streamFailure.recovery_path=$recoveryPath }
                return @{exit_code=1;output=($streamFailure | ConvertTo-Json -Depth 5 -Compress)}
            }
            $raw=$stdout.GetAwaiter().GetResult()
            # Drain stderr without returning parameter text, native diagnostics or partial output.
            [void]$stderr.GetAwaiter().GetResult()
            try {
                $reply=$raw | ConvertFrom-Json
                if ($null -eq $reply.ok -or ($reply.ok -and $process.ExitCode -ne 0)) { throw 'Invalid worker response.' }
                $exitCode=$(if ($reply.ok) { 0 } else { 1 })
                return @{exit_code=$exitCode;output=$raw.Trim()}
            } catch {
                $errorResult=[ordered]@{ok=$false;action=$Action;code='worker_failed';message='The worker did not complete with a valid JSON result; parameter contents and raw diagnostics are omitted.';input_may_be_partial=[bool]$lastPhase.input_may_be_partial}
            }
        }
        if ($Action -eq 'clipboard-text' -and (Test-Path -LiteralPath $recoveryPath -PathType Leaf)) {
            $errorResult.clipboard_restored=$false
            $errorResult.recovery_path=$recoveryPath
        }
        return @{exit_code=1;output=($errorResult | ConvertTo-Json -Depth 5 -Compress)}
    } catch {
        $failure=[ordered]@{ok=$false;action=$Action;code='worker_start_failed';message='The private UI worker could not be started or supervised; parameter contents are omitted.';input_may_be_partial=[bool]$lastPhase.input_may_be_partial}
        if ($recoveryPath -and (Test-Path -LiteralPath $recoveryPath -PathType Leaf)) { $failure.clipboard_restored=$false; $failure.recovery_path=$recoveryPath }
        return @{exit_code=1;output=($failure | ConvertTo-Json -Depth 5 -Compress)}
    } finally {
        if ($process) {
            try { if (-not $process.HasExited) { $process.Kill($true); [void]$process.WaitForExit(3000) } } catch { }
            $process.Dispose()
        }
        foreach ($temporaryPath in @($requestPath,$statusPath)) {
            if ($temporaryPath) { Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue }
        }
        # Never remove a remaining clipboard recovery file here: it may be the only original copy.
    }
}

if (-not $WorkerRequest) {
    $workerResult=Invoke-BoundedWorker $PSBoundParameters
    $workerResult.output
    exit $workerResult.exit_code
}

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { Fail 'unsupported_platform' 'This adapter requires an interactive Windows desktop.' }
    [void][IO.Directory]::CreateDirectory($stateRoot)
    try { $adapterLock = [IO.File]::Open((Join-Path $stateRoot 'operator.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
    catch { Fail 'ui_busy' 'Another adapter call owns the UI lock. Wait for it to finish; do not delete the lock file.' }
    Initialize-Native
    $dpiScope = [CampusHomework.DesktopV1]::SetThreadDpiAwarenessContext([IntPtr](-4))
    $result = [ordered]@{ ok=$true; action=$Action; time_utc=[DateTime]::UtcNow.ToString('o') }
    switch ($Action) {
        'list' { $result.windows = @(Get-VisibleWindows) }
        'launch' {
            $candidates = @(Get-VisibleWindows | Where-Object { $_.process_name -eq 'cxstudy' -or $_.title -match '学习通|Chaoxing' })
            if ($candidates.Count -gt 0) { $result.status='already_open'; $result.windows=$candidates }
            elseif ($Web) {
                $officialUrl = 'https://i.chaoxing.com/'
                Start-Process $officialUrl | Out-Null
                $result.status='official_web_open_requested'; $result.url=$officialUrl
            } else {
                $paths = [System.Collections.Generic.List[string]]::new()
                if ($Executable) {
                    if (-not [IO.Path]::IsPathRooted($Executable) -or [IO.Path]::GetFileName($Executable) -ine 'cxstudy.exe') { Fail 'invalid_executable' 'Pass an absolute path to the installed cxstudy.exe.' }
                    $paths.Add($Executable)
                }
                foreach ($base in @($env:LOCALAPPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
                    if ($base) { foreach ($relative in @('Chaoxing\cxstudy\cxstudy.exe','Chaoxing\cxstudy.exe','cxstudy\cxstudy.exe','Programs\cxstudy\cxstudy.exe')) { $paths.Add((Join-Path $base $relative)) } }
                }
                $found = @($paths | Select-Object -Unique | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
                if ($found.Count -eq 0) { Fail 'client_not_found' 'No existing window or known installation found. Pass -Executable to the installed cxstudy.exe, or use -Web for the official entry.' }
                # The requested learning client is an interactive application, not a background helper.
                $client = Start-Process -FilePath $found[0] -WindowStyle Normal -PassThru
                $result.status='client_launch_requested'; $result.pid=$client.Id; $result.executable=$found[0]
            }
        }
        'restore' {
            # A minimized window cannot supply an observation. Restoring it sends
            # no text/click and is followed by an ordinary fresh observation.
            Require-Target
            Require-Context
            [CampusHomework.DesktopV1]::Focus($WindowHandle, $ProcessId)
            $result.status='restored_observe_before_input'
            $result.requires_new_observation=$true
        }
        'observe' {
            Require-Target
            Require-Context
            if (-not $OutputPath -or -not [IO.Path]::IsPathRooted($OutputPath) -or [IO.Path]::GetExtension($OutputPath) -ine '.png') { Fail 'invalid_output' 'Pass an absolute .png -OutputPath in a local private run directory.' }
            if (-not (Test-Path -LiteralPath ([IO.Path]::GetDirectoryName($OutputPath)) -PathType Container)) { Fail 'invalid_output' 'Output directory must already exist.' }
            $geometry = Get-TargetGeometry
            $identity = Get-TargetIdentity
            Invoke-WindowCapture $OutputPath
            $after = Get-TargetGeometry
            if (($geometry | ConvertTo-Json -Compress) -cne ($after | ConvertTo-Json -Compress)) { Fail 'unstable_observation' 'Window moved during capture. Observe again.' }
            $newToken = [Guid]::NewGuid().ToString('N')
            $snapshot = Join-Path $stateRoot ($newToken + '.png')
            Copy-Item -LiteralPath $OutputPath -Destination $snapshot
            $record = [ordered]@{ target=$identity; geometry=$geometry; context=$Context; created_ms=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(); generation=(Get-InputGeneration); snapshot=$snapshot }
            $record | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $stateRoot ($newToken + '.json')) -Encoding utf8
            $result.token=$newToken; $result.target=$identity; $result.geometry=$geometry
            $result.context=$Context; $result.context_is_operator_assertion=$true; $result.screenshot=$OutputPath
            $result.foreground=[CampusHomework.DesktopV1]::Foreground($WindowHandle); $result.expires_in_seconds=120
            $result.coordinates='physical pixels from the screenshot top-left; token is single-use for input'
            # Remove only this adapter's old token records; an abandoned process does not hold the OS file lock.
            Get-ChildItem -LiteralPath $stateRoot -File | Where-Object { $_.Name -match '^[a-f0-9]{32}\.(json|png|check\.png)$' -and $_.LastWriteTimeUtc -lt [DateTime]::UtcNow.AddMinutes(-10) } | Remove-Item -Force
        }
        'automation' {
            $record = Read-Observation $false
            $result.target=$record.target; $result.context=$record.context; $result.automation=Read-Automation
        }
        default {
            $record = Read-Observation ($Action -ne 'focus')
            if ($Action -eq 'click' -and ($X -lt 0 -or $Y -lt 0 -or $X -ge $record.geometry.width -or $Y -ge $record.geometry.height)) { Fail 'out_of_bounds' 'Use local physical screenshot coordinates inside the observed window.' }
            $keyCodes = $null
            if ($Action -eq 'key') { $keyCodes = Get-KeyCodes $Key }
            if ($Action -eq 'type' -and -not $PSBoundParameters.ContainsKey('Text')) { Fail 'missing_text' 'Pass -Text, or use a text file read by the caller without shell interpolation.' }
            if ($Action -eq 'upload-file') {
                if (-not [IO.Path]::IsPathRooted($FilePath) -or -not (Test-Path -LiteralPath $FilePath -PathType Leaf)) { Fail 'invalid_file' 'Pass an existing absolute local file path.' }
                if ([CampusHomework.DesktopV1]::ClassName($WindowHandle) -ne '#32770') { Fail 'not_file_dialog' 'Target must be a newly observed standard Windows Open file dialog.' }
            }
            Consume-Observation $record
            $inputMayBePartial = $true
            Set-WorkerPhase 'input'
            switch ($Action) {
                'focus' { [CampusHomework.DesktopV1]::Focus($WindowHandle, $ProcessId) }
                'click' { [CampusHomework.DesktopV1]::Click($WindowHandle, $ProcessId, $X, $Y, ($Button -eq 'right')) }
                'key' { [CampusHomework.DesktopV1]::Keys($WindowHandle, $ProcessId, $keyCodes) }
                'type' { [CampusHomework.DesktopV1]::Type($WindowHandle, $ProcessId, $Text); $result.characters_sent=$Text.Length }
                'clipboard-text' { $result.text=Copy-SelectedText; $result.clipboard_restored=$true; $result.format='UnicodeText only' }
                'upload-file' {
                    # Standard dialog's File name mnemonic; only type after confirming an Edit control received focus.
                    [CampusHomework.DesktopV1]::Keys($WindowHandle, $ProcessId, [ushort[]]@(18,78))
                    Start-Sleep -Milliseconds 100
                    $focused = [CampusHomework.DesktopV1]::FocusedControl($WindowHandle)
                    if ($focused -eq 0 -or [CampusHomework.DesktopV1]::ClassName($focused) -notmatch 'Edit') { Fail 'file_name_field_unavailable' 'File name edit did not receive focus. Inspect the dialog; no file path was typed.' }
                    [CampusHomework.DesktopV1]::Keys($WindowHandle, $ProcessId, [ushort[]]@(17,65))
                    [CampusHomework.DesktopV1]::Type($WindowHandle, $ProcessId, (Get-Item -LiteralPath $FilePath).FullName)
                    $result.status='file_path_entered_not_confirmed'
                    $result.next_step='Observe the dialog, verify the file name, press Enter with a fresh token, then verify the platform attachment.'
                }
            }
            $result.target=$record.target; $result.context=$record.context; $result.token_consumed=$true
            $result.foreground_after=[CampusHomework.DesktopV1]::Foreground($WindowHandle)
            $result.requires_new_observation=$true
        }
    }
    $result | ConvertTo-Json -Depth 9 -Compress
} catch {
    $code = 'adapter_error'
    $exception = $_.Exception
    while ($exception) {
        if ($exception.Data.Contains('code')) { $code=[string]$exception.Data['code']; break }
        if ($exception.Message -match '(target_changed|lost_focus|capture_unavailable|input_failed|out_of_bounds|occluded_target):') { $code=$Matches[1]; break }
        $exception = $exception.InnerException
    }
    $errorResult=[ordered]@{ ok=$false; action=$Action; code=$code; message=$_.Exception.Message; input_may_be_partial=$inputMayBePartial; next_step='Inspect the current target and observe again before any retry.' }
    if ($code -eq 'stale_observation' -and $observationDifference) { foreach ($entry in $observationDifference.GetEnumerator()) { $errorResult[$entry.Key]=$entry.Value } }
    if ($code -in @('clipboard_backup_unsupported','sta_required')) { $errorResult.input_may_be_partial=$false; $errorResult.clipboard_untouched=$true }
    if ($null -ne $clipboardRestored) { $errorResult.clipboard_restored=$clipboardRestored }
    if ($clipboardRecoveryPath -and (Test-Path -LiteralPath $clipboardRecoveryPath -PathType Leaf)) { $errorResult.recovery_path=$clipboardRecoveryPath }
    $errorResult | ConvertTo-Json -Depth 4 -Compress
    exit 1
} finally {
    if ($dpiScope -ne [IntPtr]::Zero -and ('CampusHomework.DesktopV1' -as [type])) { [void][CampusHomework.DesktopV1]::SetThreadDpiAwarenessContext($dpiScope) }
    if ($adapterLock) { $adapterLock.Dispose() }
}
