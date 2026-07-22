# SPDX-License-Identifier: MIT
# winpodx debloat: telemetry & diagnostics

Write-Host "[telemetry] Disabling AllowTelemetry policy + data collection..."

$privacyValues = @(
    # Diagnostic data / telemetry level
    @{Path="HKLM:\Software\Policies\Microsoft\Windows\DataCollection"; Name="AllowTelemetry"; Value=0},
    @{Path="HKLM:\Software\Microsoft\Windows\CurrentVersion\Policies\DataCollection"; Name="AllowTelemetry"; Value=0},

    # Bluetooth advertising + experimentation
    @{Path="HKLM:\Software\Microsoft\PolicyManager\current\device\Bluetooth"; Name="AllowAdvertising"; Value=0},
    @{Path="HKLM:\Software\Microsoft\PolicyManager\current\device\System"; Name="AllowExperimentation"; Value=0},

    # Application impact telemetry (Inventory / UAR)
    @{Path="HKLM:\Software\Policies\Microsoft\Windows\AppCompat"; Name="DisableUAR"; Value=1},

    # Handwriting / ink error + data sharing
    @{Path="HKLM:\Software\Policies\Microsoft\Windows\HandwritingErrorReports"; Name="PreventHandwritingErrorReports"; Value=1},
    @{Path="HKLM:\Software\Policies\Microsoft\Windows\TabletPC"; Name="PreventHandwritingDataSharing"; Value=1},

    # Activity history / timeline upload
    @{Path="HKLM:\Software\Policies\Microsoft\Windows\System"; Name="PublishUserActivities"; Value=0},
    @{Path="HKLM:\Software\Policies\Microsoft\Windows\System"; Name="EnableActivityFeed"; Value=0},
    @{Path="HKLM:\Software\Policies\Microsoft\Windows\System"; Name="UploadUserActivities"; Value=0},

    # DiagTrack autologger + error reporting + online tips
    @{Path="HKLM:\System\CurrentControlSet\Control\WMI\Autologger\Diagtrack-Listener"; Name="Start"; Value=0},
    @{Path="HKLM:\Software\Microsoft\Windows\Windows Error Reporting"; Name="Disabled"; Value=1},
    @{Path="HKLM:\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"; Name="AllowOnlineTips"; Value=0},

    # Typing insights (inking & typing personalization)
    @{Path="HKCU:\Software\Microsoft\Input\TIPC"; Name="Enabled"; Value=0},
    @{Path="HKCU:\Software\Microsoft\InputPersonalization"; Name="RestrictImplicitInkCollection"; Value=1},
    @{Path="HKCU:\Software\Microsoft\InputPersonalization"; Name="RestrictImplicitTextCollection"; Value=1},
    @{Path="HKCU:\Software\Microsoft\InputPersonalization\TrainedDataStore"; Name="HarvestContacts"; Value=0},
    @{Path="HKCU:\Software\Microsoft\Personalization\Settings"; Name="AcceptedPrivacyPolicy"; Value=0},

    # Recently opened program tracking
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="Start_TrackProgs"; Value=0},

    # Online speech recognition
    @{Path="HKCU:\Software\Microsoft\Speech_OneCore\Settings\OnlineSpeechPrivacy"; Name="HasAccepted"; Value=0},

    # Feedback frequency (Siuf)
    @{Path="HKCU:\Software\Microsoft\Siuf\Rules"; Name="NumberOfSIUFInPeriod"; Value=0},
    @{Path="HKCU:\Software\Microsoft\Siuf\Rules"; Name="PeriodInNanoSeconds"; Value=0},

    # Cloud search history (MSA / AAD / device)
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\SearchSettings"; Name="IsMSACloudSearchEnabled"; Value=0},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\SearchSettings"; Name="IsAADCloudSearchEnabled"; Value=0},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\SearchSettings"; Name="IsDeviceSearchHistoryEnabled"; Value=0}
)

foreach ($item in $privacyValues) {
    New-Item -Path $item.Path -Force -ErrorAction SilentlyContinue | Out-Null
    Set-ItemProperty -Path $item.Path -Name $item.Name -Value $item.Value -Type DWord -Force -ErrorAction SilentlyContinue
}

Write-Host "[telemetry] Stopping DiagTrack + dmwappushservice..."
Stop-Service -Name "DiagTrack" -Force -ErrorAction SilentlyContinue
Set-Service -Name "DiagTrack" -StartupType Disabled -ErrorAction SilentlyContinue
Stop-Service -Name "diagnosticshub.standardcollector.service" -Force -ErrorAction SilentlyContinue
Set-Service -Name "diagnosticshub.standardcollector.service" -StartupType Disabled -ErrorAction SilentlyContinue
Stop-Service -Name "dmwappushservice" -Force -ErrorAction SilentlyContinue
Set-Service -Name "dmwappushservice" -StartupType Disabled -ErrorAction SilentlyContinue
