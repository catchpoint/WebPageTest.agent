Install-WindowsFeature NET-Framework-Core
Set-ExecutionPolicy Bypass -Scope Process -Force
iex ((New-Object System.Net.WebClient).DownloadString('https://chocolatey.org/install.ps1'))
iwr -Uri 'https://github.com/WPO-Foundation/wptagent/blob/master/windows_post_reboot.ps1' -OutFile- 'C:\post-reboot.ps1'
refreshenv
# Packages necessare for the wptagent Python tool to run
cinst git vcpython27 imagemagick.tool ffmpeg python2 winpcap -y /params "'/quiet'"
# We need to reboot cause PATH needs updating, and refreshenv isn't foolproof. So, new startup task, new PS file, PS file runs once & deletes the sched task.
refreshenv
schtasks /create /tn WPTPostReboot /sc onstart /delay 0000:30 /rl highest /ru system /tr "powershell.exe -file C:\post-reboot.ps1"
shutdown /r /f
