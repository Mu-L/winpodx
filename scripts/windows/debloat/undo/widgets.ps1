# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: Widgets / news panel.

Write-Host "[widgets] Restoring widgets / taskbar news panel..."

$widgetValues = @(
    # Windows 11
    @{Path="HKLM:\Software\Policies\Microsoft\Dsh"; Name="AllowNewsAndInterests"},

    # Windows 10
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Feeds"; Name="EnShellFeedsTaskbarViewMode"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Feeds"; Name="ShellFeedsTaskbarPreviousViewMode"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Feeds"; Name="ShellFeedsTaskbarContentUpdateMode"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Feeds"; Name="ShellFeedsTaskbarOpenOnHover"}
)

foreach ($item in $widgetValues) {
    Remove-ItemProperty -Path $item.Path -Name $item.Name -Force -ErrorAction SilentlyContinue
}

Write-Host "[widgets] Restoring taskbar widgets icon..."
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" -Name "TaskbarDa" -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
