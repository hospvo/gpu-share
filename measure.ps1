$ErrorActionPreference = 'Stop'
$items = @(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory)
$engines = @(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine)
$rows = foreach ($adapter in $items) {
    $groups = $engines | Where-Object { $_.Name.Contains($adapter.Name + '_') } |
        Group-Object { $_.Name -replace '^pid_\d+_', '' }
    $peak = 0
    foreach ($group in $groups) {
        $sum = ($group.Group | Measure-Object UtilizationPercentage -Sum).Sum
        $peak = [Math]::Max([double]$peak, [double]$sum)
    }
    [PSCustomObject]@{ Name=$adapter.Name; DedicatedUsage=$adapter.DedicatedUsage; Load=[Math]::Min(100, $peak) }
}
ConvertTo-Json -InputObject @($rows) -Compress
