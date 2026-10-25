# Windows desktop adapter

Supports an interactive Windows 10/11 desktop with PowerShell 7 and .NET's Windows desktop assemblies. Run clipboard operations in STA. No extra browser extension, account password, cookie extraction or private platform endpoint is used. This adapter does not understand questions or decide which assignment to submit.

Use `scripts/windows.ps1` from its installed location; examples below assume the current directory is the skill root. `-WindowHandle` and `-ProcessId` always come from the current window list. Never reuse another person's HWND, PID or coordinates.

```powershell
pwsh -NoProfile -STA -File ./scripts/windows.ps1 -Action list
pwsh -NoProfile -STA -File ./scripts/windows.ps1 -Action launch
# If no known installation is found, pass the real absolute installed cxstudy.exe path:
pwsh -NoProfile -STA -File ./scripts/windows.ps1 -Action launch -Executable $installedClient
# Or explicitly open the official web entry in the user's default browser:
pwsh -NoProfile -STA -File ./scripts/windows.ps1 -Action launch -Web
```

`launch` first returns existing learning-platform windows. Installation candidates are printed in the source: Chaoxing/cxstudy directories below LOCALAPPDATA and Program Files. A launch request is not evidence that login, course discovery or assignment navigation succeeded. Inspect the current window afterward. The web entry is `https://i.chaoxing.com/`; opening it does not create a browser debugging connection.

## Observe before input

Create a private run directory outside the public repository. Pick the target from `list`, then observe it. A context is a label the operator checked against the screenshot, such as `course/task/question-3` or `navigation`. The adapter echoes it; it cannot verify question meaning.

```powershell
$observation = pwsh -NoProfile -STA -File ./scripts/windows.ps1 -Action observe -WindowHandle $window.hwnd -ProcessId $window.pid -Context 'course/task/question-3' -OutputPath $absolutePng | ConvertFrom-Json
# Read the actual PNG before choosing local physical-pixel coordinates.
pwsh -NoProfile -STA -File ./scripts/windows.ps1 -Action click -WindowHandle $window.hwnd -ProcessId $window.pid -Context $observation.context -Token $observation.token -X $localX -Y $localY
```

Each input consumes its token and invalidates earlier observation tokens. Observe again after a click, focus change, key press, typing, clipboard copy or dialog action. Tokens expire after 120 seconds. HWND, PID, process start time, title, physical geometry, DPI and visible content are compared before input. Detectable layout or viewport changes reject the token. A tiny image-difference tolerance allows caret blinking; it is not a semantic guarantee against a subtly changed question. Always check the actual question number, task and editor yourself. A stale observation is recoverable: wait for the actual page to settle, observe again, read the new PNG, and choose the current target.

Screenshots use `PrintWindow` on the target HWND and never copy the desktop. Background windows may be observed without stealing focus. Minimized, unresponsive, uniform or unsupported GPU/CEF captures fail; a nonuniform but stale GPU frame is also possible. Read the image and use a supported window capture tool when needed. There is no automatic full-desktop screenshot fallback.

Input requires the exact target to be foreground; `focus` is the explicit exception that requests foreground. It also requires a fresh observation. Coordinates are local to the entire observed window PNG, including its frame, in physical pixels. Out-of-bounds or points covered by another window are rejected. Moving, resizing or changing DPI invalidates coordinates. Multiple monitors do not share a guessed scaling factor.

An explicit `focus` makes one foreground request while temporarily attaching the calling thread's input queue to the foreground/target threads; attachments are released in `finally`. It does not send Alt or click a guessed point. Click, key and type never reactivate a lost target themselves. After focus, observe again and verify the actual editor. A `lost_focus` error includes the actual foreground HWND/PID for diagnosis, without its title or content.

When the selected window is minimized, call `-Action restore -WindowHandle HWND -ProcessId PID -Context CONTEXT` after identifying it with `list`. This only restores/focuses that window, sends no text or click, and does not need a screenshot token. Then `observe` and inspect the fresh screenshot before any answer input. Ordinary `focus` still requires a token.

## Actions

All target actions use `-WindowHandle`, `-ProcessId`, `-Context` and `-Token` as above.

| Action | Additional arguments | Result |
| --- | --- | --- |
| `focus` | None | Requests focus, then checks it; observe again. |
| `click` | `-X`, `-Y`, optional `-Button right` | One click inside the observed window. |
| `key` | `-Key 'Ctrl+A'`, `Tab`, `Shift+Tab`, `Backspace`, `Enter`, `PageDown`, etc. | Normal keyboard input. Select-all is safe only after checking the exact answer editor. |
| `type` | `-Text $answer` | Unicode keyboard input, not clipboard paste; text is not echoed in JSON. Newlines are Enter key presses, so first confirm a multiline editor. |
| `clipboard-text` | None | Copies only the current selection with Ctrl+C; returns Unicode plain text and restores previous plain-text clipboard content. An existing rich/HTML/image/file clipboard is left untouched and the action is refused. It never selects the page automatically or returns HTML. |
| `automation` | Optional `-Limit 100` | Read-only accessible names, types, IDs and screen bounds; no ValuePattern or document HTML. Token is not consumed. |
| `upload-file` | `-FilePath $absoluteLocalFile` | Only in a newly observed standard Windows Open dialog: enters the filename using normal keys; does not confirm upload. |

If UIAutomation gives only a legacy/opaque container, use the target screenshot. Its bounds are screen coordinates; convert using the observation's `left` and `top` before a local click. A read-only enumeration can itself encounter a slow third-party accessibility provider; it is an aid, not a required extraction path.

For uploads, click the platform's normal attachment button first. List and observe the resulting dialog as its own HWND/PID. `upload-file` uses the standard File name keyboard mnemonic and checks for an Edit control before typing. If a custom dialog does not support that convention, stop and locate its actual field; do not guess coordinates. Observe the entered path, confirm with a fresh `key Enter`, and verify the attachment in the original platform window. Dialog acceptance is not proof of a completed upload. A click or key may legitimately open or close a dialog; JSON includes `foreground_after` and still requires a fresh observation.

## Focus, concurrency and recovery

Every invocation holds an exclusive per-user OS file lock under the user's temporary directory. Parallel calls fail with `ui_busy`; `finally` releases the handle, and process termination lets Windows release it. Do not delete the lock file to bypass another operator. This prevents simultaneous adapter calls; it does not reserve the desktop between calls or prevent another application from using input. Keep one UI operator for the whole workflow. Other agents may solve or review read-only.

The Unicode input loop checks HWND/PID and foreground before every character. Checks are not atomic with delivery: a focus switch can race a keystroke. Stop on any failure and inspect the affected content. A top-level foreground check cannot prove that the intended question editor has focus. Do not retry blindly or clear text in another application.

Public calls launch an isolated hidden worker with UTF-8 request/results. The child owns the OS lock. The parent terminates a worker whose capture or accessibility phase exceeds 15 seconds; normal calls also have a 15-second deadline, while type scales with text length up to 90 seconds. Request/status files are private and removed in `finally`. If clipboard restoration fails or a worker is interrupted during copy, `clipboard_restored=false` and `recovery_path` identify a private local backup of the original plain text; preserve it until recovery. The backup text is never printed in that error.

JSON errors identify stale observations, focus loss, busy lock, unsupported captures and invalid coordinates. `input_may_be_partial` means inspect before resuming. A success JSON proves only the adapter action finished; review actual content, save, attachment and submission evidence separately. Use natural input and respect any platform restriction on paste. Ordinary assignments and practice exams use the same visible UI protocol; this adapter does not bypass proctoring, authentication or page restrictions.

Token snapshots are private local runtime artifacts, retained for at most approximately ten minutes once a later observation triggers cleanup. They may contain student information. Keep run directories, screenshots, tokens and logs out of version control and public examples.

Run `tests/windows-smoke.ps1` only when the single UI operator has reserved the desktop. It uses independent local WinForms windows, not a real learning-platform account or browser tab.

The smoke test creates hidden child PowerShell processes so console windows do not steal focus. It verifies input and rejection paths against its own two windows. A failure remains a failure in its JSON evidence; unsupported clipboard or accessibility behavior must not be reported as a full successful run. No real-platform compatibility claim follows from a local WinForms test.

On this desktop, selected text in a native WinForms TextBox reproduced inconsistent `PrintWindow` drawing, even though the control's text and selection remained correct. The clipboard smoke step was refused with `stale_observation` before Ctrl+C, and the clipboard was preserved. Treat this as a capture limitation: wait and observe once more; if the image remains unstable, use a supported target-window capture method and inspect the selection. Do not disable the stale check or claim the clipboard UI step passed.
