# After PR merge: copy wrap Python into Desktop\factorbook\ and patch
# C:\Users\MLP\Desktop\factorbook.html (sibling of the pack folder).
# Does NOT copy HTML and does NOT run desk_dash / write_dash.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$packSrc = Split-Path -Parent $here
$py = Join-Path $packSrc "sync_live_paintpx.py"
$desktopDir = "C:\Users\MLP\Desktop"
$pack = Join-Path $desktopDir "factorbook"
$html = Join-Path $desktopDir "factorbook.html"
if (-not (Test-Path $py)) {
  $py = Join-Path $pack "sync_live_paintpx.py"
}
if (-not (Test-Path $py)) {
  throw "sync_live_paintpx.py not found. Copy it from factor-desk/ after merge."
}
python $py --deploy-desktop --desktop-root $pack --html $html
