$ErrorActionPreference = 'Stop'
$inputConfig = [Console]::In.ReadToEnd() | ConvertFrom-Json
$name = 'CitiFoxCharger-Private-LAN'
# Replace only our own rule. Never disable the firewall or open public profiles.
Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
$null = New-NetFirewallRule -Name $name -DisplayName 'CitiFoxCharger Heimnetz HTTPS' -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $inputConfig.host -LocalPort ([int]$inputConfig.port) -RemoteAddress LocalSubnet -Profile Private -Program $inputConfig.program
