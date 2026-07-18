param([Parameter(Mandatory=$true)][string]$Path)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $Path)) { throw "Missing: $Path" }
$full = (Resolve-Path -LiteralPath $Path).Path
$bk1 = "$full.bk1"; $bk2 = "$full.bk2"; $bk3 = "$full.bk3"
if (Test-Path -LiteralPath $bk3) {
  if (Test-Path -LiteralPath $bk1) { Remove-Item -LiteralPath $bk1 -Force }
  if (Test-Path -LiteralPath $bk2) { Move-Item -LiteralPath $bk2 -Destination $bk1 -Force }
  Move-Item -LiteralPath $bk3 -Destination $bk2 -Force
}
Copy-Item -LiteralPath $full -Destination $bk3 -Force
Write-Output "backup -> $bk3"
