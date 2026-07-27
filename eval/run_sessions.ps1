param(
    [string]$DograhRepo = "C:\Users\sumit\Downloads\Dora\dograh",
    [string]$OutDir = "eval\sessions",
    [string]$Model = "opencode/deepseek-v4-flash-free"
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $PSCommandPath
$OutDir = Join-Path $scriptRoot "sessions"
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

$prompt = Get-Content -Path (Join-Path $OutDir "task_prompt.txt") -Raw

function Run-Session {
    param([string]$Label, [string]$Branch, [bool]$EnableMCP, [int]$TimeoutSec = 600)

    Write-Output "========================================"
    Write-Output "SESSION: $Label"
    Write-Output "Branch:  $Branch"
    Write-Output "MCP:     $(if($EnableMCP){'ENABLED'}else{'DISABLED'})"
    Write-Output "Timeout: ${TimeoutSec}s"
    Write-Output "========================================"

    Push-Location $DograhRepo

    git checkout $Branch 2>&1 | Out-Null

    $mcpPath = Join-Path $DograhRepo "mcp.json"
    if (Test-Path $mcpPath) { Remove-Item $mcpPath }

    if ($EnableMCP) {
        $mcpContent = @"
{
  "mcpServers": {
    "graphdb": {
      "type": "http",
      "url": "http://127.0.0.1:7700"
    }
  }
}
"@
        $mcpContent | Set-Content -Path $mcpPath -Encoding utf8
        Write-Output "  mcp.json created for treatment"
    }

    $outFile = Join-Path $OutDir "${Label}_transcript.txt"
    $timeoutFile = Join-Path $OutDir "${Label}_timedout.txt"
    $startTime = Get-Date

    Write-Output "  Starting opencode run..."

    $proc = Start-Process -FilePath "opencode" -WindowStyle Hidden -ArgumentList @(
        "run",
        "-m", $Model,
        "--auto",
        $prompt
    ) -WorkingDirectory $DograhRepo -RedirectStandardOutput $outFile -PassThru -NoNewWindow

    $waited = $proc.WaitForExit($TimeoutSec * 1000)
    $elapsed = [math]::Round(((Get-Date) - $startTime).TotalSeconds, 1)

    if (-not $waited) {
        $proc.Kill()
        "TIMED OUT after ${elapsed}s" | Set-Content -Path $timeoutFile
        Write-Output "  TIMED OUT after ${elapsed}s"
    } else {
        Write-Output "  Completed in ${elapsed}s (exit code: $($proc.ExitCode))"
    }

    Pop-Location
    Write-Output "  Output: $outFile"
    Write-Output ""
}

# Check MCP server
$serverOk = (netstat -ano | findstr ":7700") -ne $null
if (-not $serverOk) {
    Write-Output "WARNING: MCP server not running on 7700!"
}

Write-Output ""
Write-Output "EVALUATION RUNNER"
Write-Output "================="
Write-Output "Model: $Model"
Write-Output "Dograh: $DograhRepo"
Write-Output "Output: $OutDir"
Write-Output ""

Run-Session -Label "baseline" -Branch "eval/baseline" -EnableMCP $false

if ($serverOk) {
    Run-Session -Label "treatment" -Branch "eval/treatment" -EnableMCP $true
} else {
    Write-Output "SKIPPING treatment - MCP server not available"
}

Write-Output ""
Write-Output "DONE"
Write-Output "Sessions complete. Transcripts in: $OutDir"
