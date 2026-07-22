# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: telemetry & diagnostics

Write-Host "[telemetry] Restoring AllowTelemetry policy + data collection to default..."

$privacyValues = @(
    @{Path="HKLM:\Software\Policies\Microsoft\Windows\DataCollection"; Name="AllowTelemetry"},
    @{Path="HKLM:\Software\Microsoft\Windows\CurrentVersion\Policies\DataCollection"; Name="AllowTelemetry"},
    @{Path="HKLM:\Software\Microsoft\PolicyManager\current\device\Bluetooth"; Name="AllowAdvertising"},
    @{Path="HKLM:\Software\Microsoft\PolicyManager\current\device\System"; Name="AllowExperimentation"},
    @{Path="HKLM:\Software\Policies\Microsoft\Windows\AppCompat"; Name="DisableUAR"},
    @{Path="HKLM:\Software\Policies\Microsoft\Windows\HandwritingErrorReports"; Name="PreventHandwritingErrorReports"},
    @{Path="HKLM:\Software\Policies\Microsoft\Windows\TabletPC"; Name="PreventHandwritingDataSharing"},
    @{Path="HKLM:\Software\Policies\Microsoft\Windows\System"; Name="PublishUserActivities"},
    @{Path="HKLM:\Software\Policies\Microsoft\Windows\System"; Name="EnableActivityFeed"},
    @{Path="HKLM:\Software\Policies\Microsoft\Windows\System"; Name="UploadUserActivities"},
    @{Path="HKLM:\System\CurrentControlSet\Control\WMI\Autologger\Diagtrack-Listener"; Name="Start"},
    @{Path="HKLM:\Software\Microsoft\Windows\Windows Error Reporting"; Name="Disabled"},
    @{Path="HKLM:\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"; Name="AllowOnlineTips"},
    @{Path="HKCU:\Software\Microsoft\Input\TIPC"; Name="Enabled"},
    @{Path="HKCU:\Software\Microsoft\InputPersonalization"; Name="RestrictImplicitInkCollection"},
    @{Path="HKCU:\Software\Microsoft\InputPersonalization"; Name="RestrictImplicitTextCollection"},
    @{Path="HKCU:\Software\Microsoft\InputPersonalization\TrainedDataStore"; Name="HarvestContacts"},
    @{Path="HKCU:\Software\Microsoft\Personalization\Settings"; Name="AcceptedPrivacyPolicy"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="Start_TrackProgs"},
    @{Path="HKCU:\Software\Microsoft\Speech_OneCore\Settings\OnlineSpeechPrivacy"; Name="HasAccepted"},
    @{Path="HKCU:\Software\Microsoft\Siuf\Rules"; Name="NumberOfSIUFInPeriod"},
    @{Path="HKCU:\Software\Microsoft\Siuf\Rules"; Name="PeriodInNanoSeconds"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\SearchSettings"; Name="IsMSACloudSearchEnabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\SearchSettings"; Name="IsAADCloudSearchEnabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\SearchSettings"; Name="IsDeviceSearchHistoryEnabled"}
)

foreach ($item in $privacyValues) {
    Remove-ItemProperty -Path $item.Path -Name $item.Name -Force -ErrorAction SilentlyContinue
}

Write-Host "[telemetry] Re-enabling DiagTrack + dmwappushservice..."
Set-Service -Name "DiagTrack" -StartupType Automatic -ErrorAction SilentlyContinue
Start-Service -Name "DiagTrack" -ErrorAction SilentlyContinue
Set-Service -Name "diagnosticshub.standardcollector.service" -StartupType Automatic -ErrorAction SilentlyContinue
Start-Service -Name "diagnosticshub.standardcollector.service" -ErrorAction SilentlyContinue
Set-Service -Name "dmwappushservice" -StartupType Automatic -ErrorAction SilentlyContinue
Start-Service -Name "dmwappushservice" -ErrorAction SilentlyContinue
