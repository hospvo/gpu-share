$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    $items = @(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory)
    $engines = @(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine)
    if (!$items.Count) { throw 'CIM returned no GPU memory adapters.' }
} catch {
    $cimError = $_.Exception.Message
    try {
        $samples = (Get-Counter -Counter '\GPU Adapter Memory(*)\Dedicated Usage','\GPU Engine(*)\Utilization Percentage').CounterSamples
        $items = @($samples | Where-Object { $_.Path -like '*\dedicated usage' -and $_.Status -in 0,1 } |
            Select-Object @{n='Name';e={$_.InstanceName}},@{n='DedicatedUsage';e={$_.CookedValue}})
        $engines = @($samples | Where-Object { $_.Path -like '*\utilization percentage' -and $_.Status -in 0,1 } |
            Select-Object @{n='Name';e={$_.InstanceName}},@{n='UtilizationPercentage';e={$_.CookedValue}})
        if (!$items.Count -or !$engines.Count) { throw 'No valid GPU performance samples.' }
    } catch {
        [Console]::Error.WriteLine("CIM: $cimError | Get-Counter: " + $_.Exception.Message)
        exit 1
    }
}
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
