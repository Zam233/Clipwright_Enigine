# Resolve the standalone frontend repo (shared by start.ps1 / check_env.ps1).
# Priority: CLIPWRIGHT_FRONTEND_ROOT, then a sibling Clipweight-Client directory.
function Resolve-FrontendRoot([string]$RepoRoot) {
    $fromEnv = $env:CLIPWRIGHT_FRONTEND_ROOT
    if ($fromEnv) {
        return [System.IO.Path]::GetFullPath($fromEnv)
    }
    $sibling = Join-Path (Split-Path $RepoRoot -Parent) 'Clipweight-Client'
    return [System.IO.Path]::GetFullPath($sibling)
}
