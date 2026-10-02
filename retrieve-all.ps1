<#
    retrieve-all.ps1 - run every split manifest against an org, with a safety gate.

    .\retrieve-all.ps1 -Org mydev
    .\retrieve-all.ps1 -Org mydev -ManifestDir chunks
    .\retrieve-all.ps1 -Org mydev -Resume          # skip what already succeeded

    Sequential by design. See the note on -Parallel below before you reach for it.
#>
param(
    [Parameter(Mandatory = $true)][string]$Org,
    [string]$ManifestDir = ".\chunks",
    [switch]$Resume,
    [switch]$SkipVerify,
    [switch]$Parallel,
    [switch]$Force
)

$logFile    = "retrieve_log.txt"
$successLog = "retrieve_success.txt"
$errorLog   = "retrieve_errors.txt"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $logFile -Value $line
    Write-Host $line
}

# --- the parallel trap -------------------------------------------------------
# Two retrieves running at once can both be writing the SAME .object file, because
# a CustomField manifest and a ValidationRule manifest both produce Account.object.
# Sequential, the last write wins and you lose fields. Parallel, you can get a
# half-written file. This is the exact failure this tool exists to prevent, so it
# is off unless you explicitly override it.
if ($Parallel -and -not $Force) {
    Write-Host "[REFUSED] -Parallel writes several retrieves into one tree at the same time."
    Write-Host "          Two manifests can produce the same .object file. Sequential loses"
    Write-Host "          fields; parallel can corrupt the file outright."
    Write-Host "          If you have verified no object spans two manifests, re-run with -Force."
    exit 1
}

if (-not (Test-Path $ManifestDir)) {
    Write-Host "[ERROR] Manifest directory not found: $ManifestDir"
    exit 1
}
$manifestFiles = Get-ChildItem -Path $ManifestDir -Filter "package-*.xml" | Sort-Object Name
if ($manifestFiles.Count -eq 0) {
    Write-Host "[ERROR] No package-*.xml in $ManifestDir"
    exit 1
}

# --- gate: never retrieve a split that verify-split.py rejects ---------------
if (-not $SkipVerify) {
    if (Test-Path ".\verify-split.py") {
        Write-Host "[....] Verifying the split before touching the org..."
        $src = if (Test-Path ".\package.xml") { ".\package.xml" } else { $null }
        if ($src) {
            & python .\verify-split.py $src $ManifestDir
            if ($LASTEXITCODE -ne 0) {
                Write-Host ""
                Write-Host "[REFUSED] The split is not safe to retrieve. Nothing was pulled."
                Write-Host "          Fix the split, or re-run with -SkipVerify if you know why."
                exit 1
            }
        } else {
            Write-Host "[WARN] package.xml not found - skipping verification."
        }
    }
}

if (-not $Resume) {
    Clear-Content $logFile    -ErrorAction SilentlyContinue
    Clear-Content $successLog -ErrorAction SilentlyContinue
    Clear-Content $errorLog   -ErrorAction SilentlyContinue
}
$alreadyDone = @()
if ($Resume -and (Test-Path $successLog)) {
    $alreadyDone = Get-Content $successLog
    Write-Host "[....] Resuming - $($alreadyDone.Count) manifests already succeeded."
}

$startTime = Get-Date
Write-Log "--- Starting Metadata Retrieval ---"
Write-Log "Org: $Org"
Write-Log "Manifests: $ManifestDir ($($manifestFiles.Count) files)"

& sf org display --target-org $Org --json 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Could not connect to org '$Org'."
    Write-Host "        Try: sf org login web --alias $Org"
    exit 1
}

$successCount = 0; $errorCount = 0; $skipped = 0; $i = 0
foreach ($file in $manifestFiles) {
    $i++
    $label = $file.BaseName -replace '^package-'
    $progress = "[$i/$($manifestFiles.Count)]"

    if ($alreadyDone -contains $label) {
        Write-Host "$progress $label ... [SKIP] already done"
        $skipped++; $successCount++
        continue
    }
    if ($label -match 'access|Profile|PermissionSet') {
        Write-Host "      NOTE: profiles and permission sets come back PARTIAL - they only"
        Write-Host "            carry permissions for metadata in the same manifest. Diff"
        Write-Host "            before you ever deploy this one."
    }

    Write-Host -NoNewline "$progress Retrieving: $label ... "
    $output = & sf project retrieve start --manifest $file.FullName --target-org $Org --wait 33 2>&1
    $code = $LASTEXITCODE

    if ($code -eq 0) {
        Write-Host "[OK]" -ForegroundColor Green
        Add-Content -Path $logFile    -Value "$progress $label OK"
        Add-Content -Path $successLog -Value $label
        $successCount++
    } else {
        Write-Host "[FAIL]" -ForegroundColor Red
        Add-Content -Path $logFile  -Value "$progress $label FAILED"
        Add-Content -Path $errorLog -Value "--- $label ---`n$output`n"
        $errorCount++
    }
}

$duration = New-TimeSpan -Start $startTime -End (Get-Date)
Write-Host ""
Write-Log "--- Retrieval Complete ---"
Write-Log "  Succeeded: $successCount (of which $skipped skipped)"
Write-Log "  Failed:    $errorCount"
Write-Log "  Duration:  $($duration.ToString('g'))"

if ($errorCount -gt 0) {
    Write-Host "[WARN] $errorCount failed. See $errorLog. Re-run with -Resume to retry only those." -ForegroundColor Yellow
    exit 1
} else {
    Write-Host "[DONE] All manifests retrieved." -ForegroundColor Green
}
