# Creates GimmeThatVid.lnk in the app folder: double-click to start, no console
# window, with the app icon. It does NOT touch the Start menu or Windows Search;
# that's the installer's job.
$ErrorActionPreference = "Stop"

$app = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    $found = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if (-not $found) { throw "pythonw.exe not found - is Python installed?" }
    $pythonw = $found.Source
}

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $app "GimmeThatVid.lnk"))
$link.TargetPath = $pythonw
$link.Arguments = '"' + (Join-Path $app "GimmeThatVid.pyw") + '"'
$link.WorkingDirectory = $app
$link.IconLocation = (Join-Path $app "gimmethatvid\assets\icon.ico") + ",0"
$link.Description = "GimmeThatVid - save YouTube videos as MP4 or MP3"
$link.Save()

Write-Output "Created $(Join-Path $app 'GimmeThatVid.lnk')"
Write-Output "  target : $($link.TargetPath)"
Write-Output "  args   : $($link.Arguments)"
