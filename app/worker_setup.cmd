@echo off
rem Proposal Routine Assistant - analysis worker installer (Windows). Double-click to run.
set "PW_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:PW_SELF,[Text.Encoding]::UTF8); iex ($s.Substring($s.IndexOf('#'+'PS_BEGIN')))"
echo.
pause
exit /b
#PS_BEGIN
# ---- 아래는 PowerShell 스크립트입니다 (위 cmd 부분이 이 파일을 읽어 실행) ----
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Server = '__SERVER__'
$Token = '__TOKEN__'
$Dir = Join-Path $env:LOCALAPPDATA 'ProposalWorker'
$Script = Join-Path $Dir 'proposal-worker.py'
$Log = Join-Path $Dir 'worker.log'
$Startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'ProposalWorker.lnk'

function Say($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }
function Fail($msg) { Say ''; Say "[실패] $msg" 'Red'; exit 1 }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
              [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + (Join-Path $HOME '.local\bin')
}

function Find-Python {
  $cands = @()
  foreach ($c in @('py', 'python')) {
    try {
      $a = if ($c -eq 'py') { @('-3', '-c') } else { @('-c') }
      $p = & $c @a 'import sys; print(sys.executable if sys.version_info >= (3, 9) else "")' 2>$null
      if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p.Trim() }
    } catch {}
  }
  $cands += Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe') -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | ForEach-Object FullName
  foreach ($p in $cands) {
    if ($p -and (Test-Path $p) -and $p -notlike '*WindowsApps*') {
      $w = Join-Path (Split-Path $p) 'pythonw.exe'
      if (Test-Path $w) { return $w }
    }
  }
  return $null
}

function Find-Claude {
  $c = Get-Command claude -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  $d = Join-Path $HOME '.local\bin\claude.exe'
  if (Test-Path $d) { return $d }
  return $null
}

function Claude-LoggedIn($exe) {
  try {
    $env:ANTHROPIC_API_KEY = $null
    $j = (& $exe auth status 2>$null) -join "`n" | ConvertFrom-Json
    return [bool]$j.loggedIn
  } catch { return $false }
}

Say '=============================================' 'Cyan'
Say ' 제안서 루틴 도우미 - 분석 실행기 설치' 'Cyan'
Say " 서버: $Server" 'Cyan'
Say '=============================================' 'Cyan'

# 1) Python
Say ''; Say '[1/5] Python 확인...'
$pyw = Find-Python
if (-not $pyw) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Fail 'Python이 없고 winget도 없어 자동 설치할 수 없습니다. https://www.python.org 에서 Python을 설치한 뒤 이 파일을 다시 실행하세요.'
  }
  Say '  Python이 없어 설치합니다 (1~3분)...' 'Yellow'
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
  Refresh-Path
  $pyw = Find-Python
  if (-not $pyw) { Fail 'Python 설치를 확인하지 못했습니다. PC를 다시 시작한 뒤 이 파일을 다시 실행하세요.' }
}
Say "  OK: $pyw" 'Green'

# 2) Claude Code
Say ''; Say '[2/5] Claude Code 확인...'
$claude = Find-Claude
if (-not $claude) {
  Say '  Claude Code가 없어 설치합니다 (1~2분)...' 'Yellow'
  try { Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression } catch { Fail "Claude Code 설치 실패: $_" }
  Refresh-Path
  $claude = Find-Claude
  if (-not $claude) { Fail 'Claude Code 설치를 확인하지 못했습니다. 이 파일을 다시 실행하세요.' }
}
Say "  OK: $claude" 'Green'

# 3) Claude 로그인 (처음 한 번)
Say ''; Say '[3/5] Claude 계정 로그인 확인...'
if (-not (Claude-LoggedIn $claude)) {
  Say '  브라우저가 열리면 Claude(Pro/Max) 계정으로 로그인하고 승인하세요.' 'Yellow'
  & $claude auth login
  if (-not (Claude-LoggedIn $claude)) { Fail 'Claude 로그인이 확인되지 않았습니다. 이 파일을 다시 실행하세요.' }
}
Say '  OK: 로그인됨' 'Green'

# 4) 실행기 내려받기 · 설정 저장
Say ''; Say '[4/5] 실행기 설치...'
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like '*proposal-worker.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500
try {
  Invoke-WebRequest "$Server/api/worker/script" -Headers @{ Authorization = "Bearer $Token" } -OutFile $Script -UseBasicParsing
} catch { Fail "서버에서 실행기를 받지 못했습니다($Server). 서버 주소에 접속되는지 확인하세요. $_" }
$cfg = '{"server": "' + $Server + '", "token": "' + $Token + '"}'
[IO.File]::WriteAllText((Join-Path $HOME '.proposal-worker.json'), $cfg, (New-Object Text.UTF8Encoding $false))
$un = @'
@echo off
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*proposal-worker.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
del "__STARTUP__" 2>nul
echo Proposal worker removed.
pause
'@
$un = $un.Replace('__STARTUP__', $Startup).Replace("`r`n", "`n").Replace("`n", "`r`n")
[IO.File]::WriteAllText((Join-Path $Dir 'uninstall.cmd'), $un, [Text.Encoding]::Default)  # cmd는 시스템 코드페이지(CP949)로 읽는다
Say "  OK: $Dir" 'Green'

# 5) 자동 실행 등록 · 시작 (콘솔 창 없이 pythonw로 실행)
Say ''; Say '[5/5] Windows 시작 시 자동 실행 등록 · 실행기 시작...'
$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut($Startup)
$lnk.TargetPath = $pyw
$lnk.Arguments = '"' + $Script + '"'
$lnk.WorkingDirectory = $Dir
$lnk.Description = '제안서 루틴 도우미 분석 실행기'
$lnk.Save()
$before = if (Test-Path $Log) { (Get-Item $Log).Length } else { 0 }
Start-Process -FilePath $pyw -ArgumentList ('"' + $Script + '"') -WorkingDirectory $Dir -WindowStyle Hidden

$ok = $false
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 500
  if (Test-Path $Log) {
    $fs = [IO.File]::Open($Log, 'Open', 'Read', 'ReadWrite')
    try { $fs.Seek($before, 'Begin') | Out-Null; $new = (New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8)).ReadToEnd() } finally { $fs.Close() }
    if ($new -match '\[연결됨\]') { $ok = $true; break }
    if ($new -match '\[실패\]') { Say $new 'Red'; Fail '실행기가 시작되지 않았습니다. 위 메시지를 확인하세요.' }
  }
}
Say ''
if ($ok) {
  Say '✔ 설치 완료! 실행기가 서버에 연결되었습니다.' 'Green'
  Say '  - 창 없이 백그라운드에서 동작하며, PC를 켤 때마다 자동으로 실행됩니다.'
  Say '  - 이제 웹 화면으로 돌아가면 "● 연결됨"으로 바뀝니다.'
} else {
  Say '실행기를 시작했지만 아직 서버 연결을 확인하지 못했습니다.' 'Yellow'
  Say "  잠시 뒤 웹 화면에서 연결 상태를 확인하세요. 로그: $Log"
}
Say "  - 제거하려면: $Dir\uninstall.cmd"
