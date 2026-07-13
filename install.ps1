param(
  [string]$Version = $env:PFA_VERSION,
  [string]$Repo = $env:PFA_REPO,
  [string]$InstallDir = $env:PFA_INSTALL_DIR
)
if (-not $Version) { $Version = '0.1.0' }
if (-not $Repo) { $Repo = 'Daniel-Brodsky/Protected-file-analyzer-docker' }
if (-not $InstallDir) { $InstallDir = Join-Path $HOME 'protected-file-analyzer' }
$baseUrl = if ($env:PFA_RELEASE_BASE_URL) { $env:PFA_RELEASE_BASE_URL } else { "https://github.com/$Repo/releases/download/v$Version" }
$archiveName = "protected-file-analyzer-$Version.zip"
$archiveUrl = "$baseUrl/$archiveName"
$checksumUrl = "$baseUrl/$archiveName.sha256"
$tmp = Join-Path $env:TEMP ([guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
  docker version | Out-Null
  docker compose version | Out-Null
  Invoke-WebRequest $archiveUrl -OutFile (Join-Path $tmp $archiveName)
  Invoke-WebRequest $checksumUrl -OutFile (Join-Path $tmp "$archiveName.sha256")
  $expected = (Get-Content (Join-Path $tmp "$archiveName.sha256")).Split(' ')[0]
  $actual = (Get-FileHash (Join-Path $tmp $archiveName) -Algorithm SHA256).Hash.ToLower()
  if ($actual -ne $expected.ToLower()) { throw "Checksum mismatch" }
  if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
  Expand-Archive -Path (Join-Path $tmp $archiveName) -DestinationPath $InstallDir -Force
  New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir 'runtime/jobs') | Out-Null
  New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir 'runtime/wordlists') | Out-Null
  $envPath = Join-Path $InstallDir '.env'
  if (-not (Test-Path $envPath)) { Copy-Item (Join-Path $InstallDir '.env.example') $envPath }
  $runtimeGid = if ($env:PFA_RUNTIME_GID) { $env:PFA_RUNTIME_GID } else { '10001' }
  $updatedEnv = (Get-Content $envPath) -replace 'PFA_SECRET_KEY=change-me', ('PFA_SECRET_KEY=' + [Convert]::ToBase64String((1..32 | ForEach-Object {Get-Random -Maximum 256})))
  $updatedEnv = $updatedEnv -replace 'PFA_RUNTIME_GID=.*', ('PFA_RUNTIME_GID=' + $runtimeGid)
  $updatedEnv | Set-Content $envPath
  Push-Location $InstallDir
  docker compose pull
  docker compose up -d
  docker compose ps
  Pop-Location
}
finally {
  if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
}
