param([string]$Pptx, [string]$OutDir, [int]$Width = 960)
# Export each slide to PNG through PowerPoint COM (read-only, no window). Kept ASCII for Windows PowerShell 5.1.
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $OutDir | Out-Null
Get-ChildItem $OutDir -Filter *.png | Remove-Item -Force
$app = New-Object -ComObject PowerPoint.Application
try {
    $pres = $app.Presentations.Open($Pptx, -1, 0, 0)
    $h = [int]($Width * $pres.PageSetup.SlideHeight / $pres.PageSetup.SlideWidth)
    for ($i = 1; $i -le $pres.Slides.Count; $i++) {
        $pres.Slides.Item($i).Export((Join-Path $OutDir ("s{0:D2}.png" -f $i)), "PNG", $Width, $h)
    }
    $pres.Close()
} finally {
    if ($app.Presentations.Count -eq 0) { $app.Quit() }
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null
}
