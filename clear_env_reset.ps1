$names = @('ANTHROPIC_API_KEY','ANTHROPIC_BASE_URL','ANTHROPIC_MODEL','CLAUDE_CODE_SSE_PORT','CLAUDE_CODE_MAX_CONTEXT_TOKENS','CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT')
foreach ($n in $names) {
    try {
        if (Test-Path "Env:\$n") { Remove-Item "Env:\$n" -ErrorAction SilentlyContinue }
    } catch {}
    try { [Environment]::SetEnvironmentVariable($n,$null,'User') } catch {}
    Write-Output "Cleared: $n"
}
Write-Output "\nRemaining matching env vars (session):"
Get-ChildItem Env: | Where-Object { $_.Name -match 'ANTHROPIC|GEMINI|CLAUDE' } | Format-Table Name,Value -AutoSize
