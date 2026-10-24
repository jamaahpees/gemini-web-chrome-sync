$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$target = Join-Path $root 'build/chrome-sync'
New-Item -ItemType Directory -Force $target | Out-Null
foreach ($name in @('cookie-editor.js', 'interface', 'icons', 'sync', 'LICENSE')) {
    Copy-Item (Join-Path $root $name) $target -Recurse -Force
}
# Replace @@browser_name with 'chrome' in env.js to prevent fallback warning
$envFile = Join-Path $target 'interface/lib/env.js'
if (Test-Path $envFile) {
    (Get-Content $envFile -Raw) -replace '@@browser_name', 'chrome' | Set-Content $envFile -NoNewline
}
Copy-Item (Join-Path $root 'manifest.chrome.json') (Join-Path $target 'manifest.json') -Force
# Keep private device credentials outside source; package only for this installation.
$privateConnection = Join-Path $HOME '.gemini-cookie-sync/main-profile.connection.json'
if (Test-Path $privateConnection) {
    Copy-Item $privateConnection (Join-Path $target 'sync/default-connection.json') -Force
}
New-Item -ItemType Directory -Force (Join-Path $root 'dist') | Out-Null
$zip = Join-Path $root 'dist/cookie-editor-gemini-sync-chrome.zip'
Compress-Archive -Path "$target/*" -DestinationPath $zip -Force
Write-Output $zip
