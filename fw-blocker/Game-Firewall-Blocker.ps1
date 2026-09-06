#Requires -RunAsAdministrator

<#
.SYNOPSIS
    Game Firewall Blocker.
.DESCRIPTION
    Automates the blocking of inbound/outbound TCP/UDP traffic for executables.
    Features: Automated Backups, Log Rotation, Idempotency, and Error Trapping.
.PARAMETER TargetPath
    The root directory to scan (e.g., "C:\Games").
.PARAMETER Action
    [Add|Remove|Refresh] - Defines the operational state.
.PARAMETER ExcludesPath
    Path to a text file containing executable names to exclude from blocking.
#>

param (
    [Parameter(Mandatory=$true, HelpMessage="Enter the path to the game folder.")]
    [ValidateScript({Test-Path $_ -PathType Container})]
    [string]$TargetPath,

    [ValidateSet("Add", "Remove", "Refresh")]
    [string]$Action = "Refresh",

    [string]$ExcludesPath = "",
   
    [string]$BackupFolder = "$env:TEMP\fw-blocker\backups",
    [string]$LogFile = "$env:TEMP\fw-blocker\fw_blocker.log"
)

# --- 0. Self-Elevation Check ---
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning "Re-launching as Administrator..."
    $scriptPath = $MyInvocation.MyCommand.Path
    if (-not $scriptPath) { $scriptPath = $PSCommandPath }
    
    $argList = @("-ExecutionPolicy Bypass", "-File `"$scriptPath`"")
    foreach ($key in $PSBoundParameters.Keys) {
        $argList += "-$key `"$($PSBoundParameters[$key])`""
    }
    
    Start-Process powershell -ArgumentList $argList -Verb RunAs
    exit
}

# --- 1. Logging and Initialization ---
$logDir = Split-Path $LogFile -Parent
if (-not (Test-Path $logDir)) { New-Item $logDir -ItemType Directory | Out-Null }

if ([string]::IsNullOrEmpty($ExcludesPath)) {
    $scriptDir = Split-Path $PSCommandPath -Parent
    $ExcludesPath = Join-Path $scriptDir "excludes.txt"
}

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logEntry = "[$timestamp] [$Level] $Message"
    $logEntry | Out-File -FilePath $LogFile -Append
   
    $color = switch($Level) {
        "ERROR" { "Red" }
        "WARN"  { "Yellow" }
        "SUCCESS" { "Green" }
        default { "Gray" }
    }
    Write-Host $logEntry -ForegroundColor $color
}

# --- 2. Backup and Maintenance ---
try {
    if (-not (Test-Path $BackupFolder)) { New-Item $BackupFolder -ItemType Directory | Out-Null }
   
    # Backup current state with rule count verification
    $backupPath = Join-Path $BackupFolder "FW_Backup_$(Get-Date -Format 'yyyyMMdd_HHmm').xml"
    $toBackup = Get-NetFirewallRule -DisplayName "Grabbed Game*" -ErrorAction SilentlyContinue
    $ruleCount = if ($toBackup) { @($toBackup).Count } else { 0 }
    
    if ($ruleCount -gt 0) {
        $toBackup | Export-Clixml $backupPath
        Write-Log "Pre-flight: Backed up $ruleCount existing Grabbed Game rules to $backupPath"
    } else {
        Write-Log "Pre-flight: No existing rules found to back up."
    }
   
    # Clean backups older than 30 days
    Get-ChildItem $BackupFolder -Filter "*.xml" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item
    Write-Log "Pre-flight: Maintenance check and backup completed."
} catch {
    Write-Log "Failed to initialize backup: $($_.Exception.Message)" "ERROR"
}

# --- 3. Rule Querying and State Helpers ---
function Get-GrabbedRulesState {
    Write-Log "Querying existing Grabbed Game firewall rules (optimised batch query)..."
    
    $ruleMap = @{} # Program (FullName) -> List of rule InstanceIDs (Name)
    $displayNameSet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    
    $rules = Get-NetFirewallRule -DisplayName "Grabbed Game*" -ErrorAction SilentlyContinue
    if ($rules) {
        foreach ($r in $rules) {
            $null = $displayNameSet.Add($r.DisplayName)
        }
        
        # Batch query all application filters in a single pipeline call
        $filters = $rules | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue
        if ($filters) {
            foreach ($f in $filters) {
                $prog = $f.Program
                if ($prog) {
                    if (-not $ruleMap.ContainsKey($prog)) {
                        $ruleMap[$prog] = [System.Collections.Generic.List[string]]::new()
                    }
                    $null = $ruleMap[$prog].Add($f.InstanceID)
                }
            }
        }
    }
    
    return [PSCustomObject]@{
        RulesMap       = $ruleMap
        DisplayNameSet = $displayNameSet
    }
}

function Get-TargetExecutables {
    param (
        [string]$Path,
        [string]$ExcludePath
    )

    $excludes = @()
    if (Test-Path $ExcludePath) {
        $excludes = @(Get-Content $ExcludePath | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        Write-Log "Loaded $($excludes.Count) exclusions from $ExcludePath"
    }

    Write-Log "Scanning directory for executables: $Path"
    return Get-ChildItem $Path -Filter "*.exe" -Recurse | Where-Object {
        $name = $_.Name
        if ($excludes -contains $name) {
            Write-Log "Skipping excluded executable: $name" "WARN"
            return $false
        }
        return $true
    }
}

function Remove-GrabbedRules {
    param (
        [string]$Path,
        [System.IO.FileInfo[]]$Executables,
        [hashtable]$RulesMap
    )
    Write-Log "Removing existing rules for target path: $Path"
   
    $normalizedPath = $Path.TrimEnd('\')
    
    # 1. Build a HashSet of the exact full paths of executables we scanned
    $targetExePaths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    if ($Executables) {
        foreach ($file in $Executables) {
            $null = $targetExePaths.Add($file.FullName)
        }
    }
    
    # 2. Match rule IDs by exact executable path or directory structure
    $ruleIdsToRemove = [System.Collections.Generic.List[string]]::new()
    foreach ($progPath in $RulesMap.Keys) {
        if ($targetExePaths.Contains($progPath) -or $progPath -eq $normalizedPath -or $progPath -like "$normalizedPath\*") {
            $ruleIdsToRemove.AddRange($RulesMap[$progPath])
        }
    }
   
    if ($ruleIdsToRemove.Count -gt 0) {
        if ($PSCmdlet.ShouldProcess("Purge $($ruleIdsToRemove.Count) firewall rules associated with $Path")) {
            $ruleIdsToRemove | Remove-NetFirewallRule -ErrorAction SilentlyContinue
            Write-Log "Successfully purged $($ruleIdsToRemove.Count) rules for folder: $Path." "SUCCESS"
        }
    } else {
        Write-Log "No existing firewall rules found for folder: $Path."
    }
}

function Add-GrabbedRules {
    param (
        [string]$Path,
        [System.IO.FileInfo[]]$Executables,
        [System.Collections.Generic.HashSet[string]]$DisplayNameSet
    )
   
    if (-not $Executables) {
        Write-Log "No executables found to process." "WARN"
        return
    }

    $rootName = Split-Path $Path -Leaf
    $createdCount = 0
    $skippedCount = 0
    
    foreach ($file in $Executables) {
        try {
            $rel = @($file.FullName.Substring($Path.Length).TrimStart('\').Split('\'))
           
            # Use root folder name plus first subdirectory if deep-nested
            if ($rel.Length -ge 2) {
                $label = "$rootName - $($rel[0])"
            } else {
                $label = $rootName
            }

            $baseName = "Grabbed Game - $label - $($file.Name)"
            $createdRule = $false
           
            foreach ($dir in "Inbound","Outbound") {
                foreach ($prot in "TCP","UDP") {
                    $displayName = "$baseName ($dir $prot)"
                    
                    if ($DisplayNameSet -and $DisplayNameSet.Contains($displayName)) {
                        continue
                    }
                    
                    if ($PSCmdlet.ShouldProcess("Create firewall rule: $displayName")) {
                        $ruleParams = @{
                            DisplayName = $displayName
                            Direction   = $dir
                            Action      = "Block"
                            Program     = $file.FullName
                            Protocol    = $prot
                            Profile     = "Any"
                            ErrorAction = "Stop"
                        }
                        New-NetFirewallRule @ruleParams | Out-Null
                        $createdCount++
                        $createdRule = $true
                    }
                }
            }
            if ($createdRule) {
                Write-Log "Created rules for: $($file.Name)"
            } else {
                $skippedCount++
                Write-Log "Rules already exist for: $($file.Name) (skipping)"
            }
        } catch {
            Write-Log "Failed to create rule for $($file.Name): $($_.Exception.Message)" "WARN"
        }
    }
    
    Write-Log "Add summary: Created $createdCount rules, skipped $skippedCount files (already blocked)." "SUCCESS"
}

# --- 4. Main Execution Pipeline ---
Write-Log "Starting Operation: $Action on $TargetPath" "INFO"

$targetExes = Get-TargetExecutables -Path $TargetPath -ExcludePath $ExcludesPath

# Fetch firewall rule state once at the start of the pipeline
$rulesState = Get-GrabbedRulesState

switch ($Action) {
    "Remove"  { 
        Remove-GrabbedRules -Path $TargetPath -Executables $targetExes -RulesMap $rulesState.RulesMap 
    }
    "Add"     { 
        Add-GrabbedRules -Path $TargetPath -Executables $targetExes -DisplayNameSet $rulesState.DisplayNameSet 
    }
    "Refresh" { 
        Remove-GrabbedRules -Path $TargetPath -Executables $targetExes -RulesMap $rulesState.RulesMap
        
        # Re-fetch rule state after removal to ensure Add has a clean starting point for this game
        $rulesStateAfterRemove = Get-GrabbedRulesState
        Add-GrabbedRules -Path $TargetPath -Executables $targetExes -DisplayNameSet $rulesStateAfterRemove.DisplayNameSet 
    }
}

Write-Log "Operation completed successfully." "SUCCESS"
