# Requirements
wptagent currently supports Windows, Linux and OSX for desktop browsers as well as Android and iOS for mobile devices (mobile testing requires a host computer connected to drive the testing).  If local traffic-shaping is enabled the agent will need to be able to elevate to admin/root. It is recommended that the agent itself not run as admin/root but that it can elevate without prompting which means disabling UAC on windows or adding the user account to the sudoers file on Linux and OSX (NOPASSWD in visudo).

## Software Requirements
* Python 3.x available on the path with the following modules installed (all available through pip):
    * dnspython
    * monotonic
    * pillow
    * psutil
    * pypiwin32 (Windows only)
    * pyobjc (Mac only)
    * requests
    * ujson
    * tornado
    * wsaccel
    * fonttools
    * future
    * bind9utils (Linux only, for rndc)
    * selenium
    * usbmuxwrapper
* Imagemagick installed and available in the path
    * The legacy tools (convert, compare, etc) need to be installed which may be optional on Windows
* ffmpeg installed and available in the path
* traceroute (Mac and Linux)
* Chrome Browser
    * Linux stable, beta and unstable channels on Ubuntu/Debian:
        * ```wget -q -O - https://www.webpagetest.org/keys/google/linux_signing_key.pub | sudo apt-key add -```
        * ```sudo sh -c 'echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list'```
        * ```sudo apt-get update```
        * ```sudo apt-get install -y google-chrome-stable google-chrome-beta google-chrome-unstable```
* Firefox Browser
    * Linux stable and nightly builds on Ubuntu/Debian:
        * ```sudo apt-get install -y software-properties-common python-software-properties```
        * ```sudo add-apt-repository -y ppa:ubuntu-mozilla-daily/ppa```
        * ```sudo apt-get update```
        * ```sudo apt-get install -y firefox firefox-trunk```
        * ```sudo dbus-uuidgen --ensure```
* Opera Browser
    * Linux stable, beta and developer builds on Ubuntu/Debian:
        * ```wget -qO- https://www.webpagetest.org/keys/opera/archive.key | sudo apt-key add -```
        * ```sudo add-apt-repository -y 'deb https://deb.opera.com/opera-stable/ stable non-free'```
        * ```sudo add-apt-repository -y 'deb https://deb.opera.com/opera-beta/ stable non-free'```
        * ```sudo add-apt-repository -y 'deb https://deb.opera.com/opera-developer/ stable non-free'```
        * ```sudo apt-get update```
        * ```sudo DEBIAN_FRONTEND=noninteractive apt-get install -yq opera-stable opera-beta opera-developer```

## For lighthouse testing
* NodeJS
    * Ubuntu/Debian:
        * ```curl -sL https://deb.nodesource.com/setup_12.x | sudo -E bash -```
        * ```sudo apt-get install -y nodejs```
* The lighthouse npm module
    * ```sudo npm install -g lighthouse```

## Remote traffic-shaping
wptagent supports configuring an external FreeBSD network bridge for traffic-shaping.  This is particularly useful for mobile device testing. For further details, see the [remote traffic shaping documentation](./remote_trafficshaping.md).

## OS Notes
### Linux
* There are time when the default file handle limits are too small (particularly when testing Firefox).  Upping the limits in /etc/security/limits.conf (at the end of the file) can help:
    * ```* soft nofile 250000```
    * ```* hard nofile 300000```
* By default Linux will take 1-2 minutes to time out on socket connections.  You can lower it to 20 seconds to fail faster (and match windows) by configuring the retries in /etc/sysctl.conf:
    * ```net.ipv4.tcp_syn_retries = 4```
* If you are seeing slow DNS resolution it could be that the test machine has IPv6 configuration issues and disabling IPv6 may fix it.  Add the following to /etc/sysctl.conf:
    * ```net.ipv6.conf.all.disable_ipv6 = 1```
    * ```net.ipv6.conf.default.disable_ipv6 = 1```
    * ```net.ipv6.conf.lo.disable_ipv6 = 1```

#### Running the agent as a systemd service

You can run the agent as a systemd service with the following unit description:

```
[Unit]
Description=wptagent

[Service]
ExecStart=/usr/bin/python <path-to-wptagent.py> --server <SERVER>/work/ --location <LOCATION> <OPTIONAL-COMMANDS>
Restart=always
TimeoutStopSec=300

[Install]
WantedBy=default.target
```

Keeping the agent up to date can then be achieved by installing a cron job for the script located under `scripts/updateAgentAndRebootSystem.sh`. This script checks if there are new commits on the master branch and pulls new changes. Afterwards a system reboot is triggered. The service will try to stop gracefully and eventually stops after 300 seconds as it is configured under `TimeoutStopSec`.

An example cron job would be every night:
```bash
15 0 * * * <PATH-TO-UPDATE-SCRIPT> <PATH-TO-WPTAGENT-DIRECTORY> >> /var/log/wptagent_updater.log
```

### Windows
* To install some of the python dependencies you may need [Visual C++ for python](http://aka.ms/vcpython27) to be installed.
* ImageMagick includes ffmpeg in the path automatically but not the latest version.  It is usually easiest to just copy the [static ffmpeg build](https://ffmpeg.zeranoe.com/builds/) over the one installed by ImageMagick.
* Disable secure boot in the bios if enabled, otherwise traffic-shaping will not be available.
* Disable UAC so it doesn't prompt when admin commands need to be run
* For pre-Windows 8.1 (or server 2012 R2) test agents, Install the DUMMYNET ipfw driver
    * If you are installing on 64-bit Windows, right-click on  "testmode.cmd" in the internal\support\dummynet\x64 folder and select "Run as Administrator".  Reboot the system to enable testmode.  If you do not run this then traffic shaping will not work after a reboot.
    * Pull up the properties for the Network Adapter that is used to access the Internet
    * Click "Install"
    * Select "Service" and click "Add"
    * Click "Have Disk" and navigate to internal\support\dummynet\x64 (or x86 for 32-bit Windows)
    * Select the ipfw+dummynet service (and click through any warnings about the driver being unsigned)
* Install [NPCap](https://nmap.org/npcap/) and configure it to start automatically as a service with the winpcap compatible interface, not restricted to administrators and not supporting loopback (all of the defaults except uncheck loopback support)
    * Works best if there is only one active network interface available on the machine, otherwise packet capture may latch on to the wrong interface.
* Make sure all of the security zones in IE have the same setting for the "protected mode" checkbox (all disabled is recommended)
* Consider using [browser-install](https://github.com/WPO-Foundation/browser-install) to keep the browsers up to date
* Running the agent from a batch file configured to start at user login (task scheduler) is a good way to run a headless agent.  Here is the batch file the public instance uses (reboots the system daily):
```bat
@echo off
cd C:\Users\WebPageTest\browser-install
git pull origin release
python.exe C:\Users\WebPageTest\browser-install\browser_install.py -vvvv --all
cd C:\Users\WebPageTest\wptagent
call npm i -g lighthouse
FOR /L %%x IN (1, 1, 24) DO (
git pull origin master
python.exe C:\Users\WebPageTest\wptagent\wptagent.py -vvvv --server "http://www.example.com/work/" --location Location_ID --key Location_key --exit 60
)
shutdown /r /f
```

### OS X (Mac)
* Xcode should be installed as well as [Network Link Conditioner (additional download)](https://swiftmania.io/network-link-conditioner/#simulator). Launch XCode and accept the license.
* The user accounts needs to be able to run sudo commands without prompting for password:
```bash
echo "${USER} ALL=(ALL:ALL) NOPASSWD:ALL" | sudo tee "/etc/sudoers.d/wptagent"
```
* The library dependencies should be installed through homebrew ([using a rosetta 2 terminal](https://stackoverflow.com/questions/64882584/how-to-run-the-homebrew-installer-under-rosetta-2-on-m1-macbook) if running on ARM):
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install libvpx ffmpeg imagemagick geckodriver ios-webkit-debug-proxy node git
```
* Python3 and pip3 should have been installed as part of the homebrew install. The Python 3 libraries should be installed through pip3:
```bash
pip3 install PyObjC ujson dnspython monotonic pillow psutil requests tornado wsaccel fonttools selenium future usbmuxwrapper
```
* Install lighthouse through npm
```bash
npm -g install lighthouse
```
* Manually install the browsers you want to use for testing. Chrome release channels can all be installed side-by-side. Firefox can only have one channel installed.
* Some permissions need to be manually configured to give the agent the ability to record the screen and manipulate the simulator (System Preferences -> Security & Privacy):
  * "Screen Recording" - Add the terminal app that is used to run the agent (i.e. iTerm or Terminal)
  * "Accessibility" - Add both scripts in <wptagent>/internal/support/osx
* The iOS simulator devices are automatically detected at startup and the browser names will be listed as part of the agent startup

### Raspberry Pi
The Raspberry Pi largely follows the Linux installation notes but there are a few additional config items that can be useful:
1. Overclock the SD Card reader (increases performance for most modern SD cards)
    * Add ```dtparam=sd_overclock=100``` to /boot/config.txt
1. Trim the sd card periodically to keep performance from degrading.
    * Add ```sudo fstrim -v /``` to the script that runs the agent or to crontab
1. Enable the hardware watchdog to reboot the device in case of hangs.
    * Add ```dtparam=watchdog=on``` to /boot/config.txt
    * Install the watchdog service: ```sudo apt-get install watchdog```
    * Modify /etc/watchdog.conf
        * Uncomment ```watchdog-device = /dev/watchdog```
        * Uncomment ```max-load-1 = 24```
        * Add ```watchdog-timeout=15```
    * Start the watchdog service in a startup script ```sudo service watchdog restart``` (or fix the init script so it can be installed)
1. Install a [static build of ffmpeg](https://johnvansickle.com/ffmpeg/) to /usr/bin (use the armel build)

## iOS testing
iOS testing requires an iOS device running [iWptBrowser](https://github.com/WPO-Foundation/iWptBrowser/blob/master/docs/walkthrough.md) (a Safari shell), the --iOS command-line flag and is supported on Mac as well as Linux hosts for controlling the device (a Raspberry Pi is recommended).

Local traffic-shaping isn't supported so either "none" or "external" needs to be specified for --shaper.

On Linux the usbmuxd support libraries should be installed automatically but if there are problems talking to the device you may need to install them manually.  To verify connectivity to the device you can run "ideviceinfo" from the command-line to make sure things are working.

## Configuration
The default browser locations for Chrome, Firefox (Stable, Beta and Canary/Nightly), Microsoft Edge and Internet Explorer will automatically be detected.  If you need to support different locations or provide different browsers you can rename browsers.ini.sample to browsers.ini and define the browser locations.

On Linux, make sure to point to the actual chrome binary and not the symlink.  Usually something like:
```
[Chrome]
exe=/opt/google/chrome/chrome
```
