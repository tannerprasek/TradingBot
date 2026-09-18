# After PR merge: copy wrap Python onto Desktop\factorbook and patch
# C:\Users\MLP\Desktop\factorbook.html (NOT Desktop\factorbook\factorbook.html).
# Does NOT copy HTML and does NOT run desk_dash / write_dash.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pack = Split-Path -Parent $here
$py = Join-Path $pack "sync_live_paintpx.py"
$desktopPack = "C:\Users\MLP\Desktop\factorbook"
if (-not (Test-Path $py)) {
  $pack = $desktopPack
  $py = Join-Path $pack "sync_live_paintpx.py"
}
if (-not (Test-Path $py)) {
  throw "sync_live_paintpx.py not found. Copy it from factor-desk/ after merge."
}
python $py --deploy-desktop --desktop-root $desktopPack
