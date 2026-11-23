# 他の PC（Windows）で PowerShell から実行する。localai-mcp を Claude Code / Codex に登録し、A30 サーバーの AI を使えるようにする。
#
# 使い方:  powershell -ExecutionPolicy Bypass -File setup-remote.ps1 -ServerIp <サーバーのIP> [-SkipCheck]
#   例: -ServerIp 192.168.1.10
#   -ServerIp は環境変数 LOCALAI_SERVER でも指定できる。
#
# やること（何度実行してもよい。2 回目以降は登録を新しい値で入れ直す）:
#   1. localai_mcp.py を %LOCALAPPDATA%\localai-mcp\ に写す（サーバーの共有が外れても動くように）
#   2. 起動に使う Python を決める（py -3 / python が 3.10 以上ならそれ、無ければ uv run --python 3.12）
#   3. claude / codex があれば MCP「localai」として登録し、タイムアウトを長くする
#   4. 疎通確認: 入口ページ（8080）と、稼働中のサービスのポートに届くか
#
# このファイルは BOM 付き UTF-8。Windows PowerShell 5.1 が日本語を読み違えないため。
param(
    [string]$ServerIp = $env:LOCALAI_SERVER,
    [switch]$SkipCheck
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ServerIp)) {
    Write-Error "サーバーの IP が未指定です。-ServerIp か環境変数 LOCALAI_SERVER で指定してください（例: -ServerIp 192.168.1.10）。" -ErrorAction Continue
    exit 2
}

$sourceFile = Join-Path (Split-Path -Parent $PSCommandPath) "..\localai_mcp.py"
$installDir = if ($env:LOCALAI_MCP_HOME) { $env:LOCALAI_MCP_HOME } else { Join-Path $env:LOCALAPPDATA "localai-mcp" }
$targetFile = Join-Path $installDir "localai_mcp.py"

# 1. 写す
if (-not (Test-Path $sourceFile)) {
    Write-Error "localai_mcp.py が見つかりません: $sourceFile（tools\localai-mcp\remote\ から実行してください）" -ErrorAction Continue
    exit 1
}
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Copy-Item -Force $sourceFile $targetFile
Write-Output "[1/4] $targetFile に写しました。"

# 2. Python（3.10 以上の実体のパスを取る。WindowsApps の python.exe は Store を開くだけの偽物のことがあるので、実際に動かして確かめる）
function Get-PythonExe {
    param([string[]]$Command)
    $exe = $Command[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { return $null }
    $rest = @()
    if ($Command.Length -gt 1) { $rest = $Command[1..($Command.Length - 1)] }
    try {
        $out = & $exe @rest -c "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')" 2>$null
    } catch {
        return $null
    }
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($out)) { return $null }
    return ($out | Select-Object -Last 1).Trim()
}
$launcher = @()
$pythonExe = Get-PythonExe @("py", "-3")
if (-not $pythonExe) { $pythonExe = Get-PythonExe @("python") }
if ($pythonExe) {
    $launcher = @($pythonExe, $targetFile)
    Write-Output "[2/4] 起動は $pythonExe"
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    $uvExe = (Get-Command uv).Source
    $launcher = @($uvExe, "run", "--no-project", "--python", "3.12", $targetFile)
    Write-Output "[2/4] Python 3.10 以上が無いので、uv run --python 3.12 で起動します（初回は uv が Python を取得します）。"
} else {
    Write-Output "[2/4] Python 3.10 以上も uv もありません。どちらかを入れてから再実行してください。"
    Write-Output "      uv: powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`""
    exit 1
}

# settings.json の env にタイムアウトを足す（Windows PowerShell 5.1 でも動くよう -AsHashtable を使わない）
function Set-ClaudeTimeouts {
    $settings = Join-Path $HOME ".claude\settings.json"
    if (Test-Path $settings) {
        $data = Get-Content $settings -Raw -Encoding UTF8 | ConvertFrom-Json
    } else {
        $data = New-Object PSObject
    }
    if (-not ($data.PSObject.Properties.Name -contains "env")) {
        $data | Add-Member -NotePropertyName env -NotePropertyValue (New-Object PSObject)
    }
    $changed = $false
    foreach ($pair in @(@("MCP_TOOL_TIMEOUT", 2100000), @("MCP_TIMEOUT", 30000))) {
        $name = $pair[0]; $wanted = $pair[1]
        $current = 0
        if ($data.env.PSObject.Properties.Name -contains $name) {
            [int]::TryParse([string]$data.env.$name, [ref]$current) | Out-Null
        }
        if ($current -lt $wanted) {
            $data.env | Add-Member -Force -NotePropertyName $name -NotePropertyValue ([string]$wanted)
            $changed = $true
        }
    }
    if ($changed) {
        New-Item -ItemType Directory -Force -Path (Split-Path $settings) | Out-Null
        $json = $data | ConvertTo-Json -Depth 50
        [System.IO.File]::WriteAllText($settings, $json + "`n", (New-Object System.Text.UTF8Encoding($false)))
        Write-Output "      settings.json の env に MCP_TOOL_TIMEOUT / MCP_TIMEOUT を足しました（他の設定は無傷）。"
    } else {
        Write-Output "      settings.json のタイムアウトは既に足りています。"
    }
}

# 3. MCP 登録
if (Get-Command claude -ErrorAction SilentlyContinue) {
    & claude mcp remove -s user localai *> $null
    if ($LASTEXITCODE -ne 0) { Write-Verbose "claude: localai は未登録だった（初回）" }
    & claude mcp add -s user localai -e "LOCALAI_SERVER=$ServerIp" -- @launcher
    if ($LASTEXITCODE -ne 0) { throw "claude mcp add が失敗しました（終了コード $LASTEXITCODE）" }
    Write-Output "[3/4] Claude Code: localai を登録しました（user スコープ）。"
    try {
        Set-ClaudeTimeouts
    } catch {
        Write-Output "      settings.json を書き換えられませんでした（$($_.Exception.Message)）。手で env に MCP_TOOL_TIMEOUT=2100000, MCP_TIMEOUT=30000 を足してください。"
    }
} else {
    Write-Output "[3/4] claude コマンドが無いので Claude Code の登録は省略。"
}

if (Get-Command codex -ErrorAction SilentlyContinue) {
    & codex mcp remove localai *> $null
    if ($LASTEXITCODE -ne 0) { Write-Verbose "codex: localai は未登録だった（初回）" }
    & codex mcp add localai --env "LOCALAI_SERVER=$ServerIp" -- @launcher
    if ($LASTEXITCODE -ne 0) { throw "codex mcp add が失敗しました（終了コード $LASTEXITCODE）" }
    Write-Output "      Codex: localai を登録しました。"
    $codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
    $codexCfg = Join-Path $codexHome "config.toml"
    if (Test-Path $codexCfg) {
        $toml = Get-Content $codexCfg -Raw -Encoding UTF8
        $section = [regex]::Match($toml, "(?ms)^\[mcp_servers\.localai\]\r?\n(?:(?!^\[).)*")
        if ($section.Success -and $section.Value -notmatch "tool_timeout_sec") {
            $newSection = $section.Value.TrimEnd("`r", "`n") + "`ntool_timeout_sec = 2100`nstartup_timeout_sec = 30`n"
            $toml = $toml.Substring(0, $section.Index) + $newSection + $toml.Substring($section.Index + $section.Length)
            [System.IO.File]::WriteAllText($codexCfg, $toml, (New-Object System.Text.UTF8Encoding($false)))
            Write-Output "      config.toml の [mcp_servers.localai] にタイムアウトを足しました。"
        }
    }
} else {
    Write-Output "      codex コマンドが無いので Codex の登録は省略。"
}

# 4. 疎通確認
if ($SkipCheck) {
    Write-Output "[4/4] -SkipCheck 指定のため疎通確認を省きます。"
    exit 0
}
Write-Output "[4/4] 疎通確認:"
$env:LOCALAI_SERVER = $ServerIp
$env:PYTHONIOENCODING = "utf-8"
$checkExe = $launcher[0]
$checkArgs = @()
if ($launcher.Length -gt 1) { $checkArgs = $launcher[1..($launcher.Length - 1)] }
& $checkExe @checkArgs --check
if ($LASTEXITCODE -eq 0) {
    Write-Output "完了。Claude Code / Codex を開き直すと localai のツール（localai_status など）が使えます。"
} else {
    Write-Output "届かないものがあります。上の NG の行を見てください（サーバーの ufw・サービスの起動）。登録は済んでいます。"
    exit 1
}
