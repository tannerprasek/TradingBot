# Patch live Desktop factorbook.html with the paintPxChart MA-regime wrap.
# Does NOT run desk_dash / write_dash (no skinny generator HTML).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pack = Split-Path -Parent $here
$py = Join-Path $pack "sync_live_paintpx.py"
if (-not (Test-Path $py)) {
  $pack = "C:\Users\MLP\Desktop\factorbook"
  $py = Join-Path $pack "sync_live_paintpx.py"
}
$html = "C:\Users\MLP\Desktop\factorbook\factorbook.html"
if (-not (Test-Path $html)) {
  $html = Join-Path $pack "factorbook.html"
}
python $py --html $html
