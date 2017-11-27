# Requirements
wptagent currently supports Windows, Linux and OSX for desktop browsers as well as Android and iOS for mobile devices (mobile testing requires a host computer connected to drive the testing).  If local traffic-shaping is enabled the agent will need to be able to elevate to admin/root. It is recommended that the agent itself not run as admin/root but that it can elevate without prompting which means disabling UAC on windows or adding the user account to the sudoers file on Linux and OSX (NOPASSWD in visudo).

## Software Requirements
* Python 2.7 available on the path (python2.7 and  python-pip packages on Ubuntu/Debian) with the following modules installed (all available through pip):
    * dnspython
    * monotonic
    * pillow
    * psutil
    * pypiwin32 (Windows only)
    * requests
    * ujson
    * tornado
    * xvfbwrapper (Linux only)
    * bind9utils (Linux only, for rndc)
    * marionette_driver (Firefox)
    * selenium (Windows only)
* Imagemagick installed and available in the path
    * The legacy tools (convert, compare, etc) need to be installed which may be optional on Windows
* ffmpeg installed and available in the path
* Xvfb (Linux only)
* cgroup-tools (Linux only if mobile CPU emulation is desired)
* traceroute (Mac and Linux)
* Debian:
    * ```sudo apt-get install -y python2.7 python-pip imagemagick ffmpeg xvfb dbus-x11 cgroup-tools traceroute && sudo pip install dnspython monotonic pillow psutil requests ujson tornado xvfbwrapper marionette_driver```
* Chrome Browser
    * Linux stable, beta and unstable channels on Ubuntu/Debian:
        * ```wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | sudo apt-key add -```
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
        * ```wget -qO- https://deb.opera.com/archive.key | sudo apt-key add -```
        * ```sudo add-apt-repository -y 'deb https://deb.opera.com/opera-stable/ stable non-free'```
        * ```sudo add-apt-repository -y 'deb https://deb.opera.com/opera-beta/ stable non-free'```
        * ```sudo add-apt-repository -y 'deb https://deb.opera.com/opera-developer/ stable non-free'```
        * ```sudo apt-get update```
        * ```sudo DEBIAN_FRONTEND=noninteractive apt-get install -yq opera-stable opera-beta opera-developer```

## For lighthouse testing
* NodeJS
    * Ubuntu/Debian:
        * ```curl -sL https://deb.nodesource.com/setup_9.x | sudo -E bash -```
        * ```sudo apt-get install -y nodejs```
* The lighthouse npm module
    * ```sudo npm install -g lighthouse```

## Remote traffic-shaping
wptagent supports configuring an external FreeBSD network bridge for traffic-shaping.  This is particularly useful for mobile device testing where the devices can be connected to a WiFi access point and the access point is connected through a FreeBSD bridge to get to the network.

i.e. mobile phone <--> WiFi <--> FreeBSD bridge <--> Internet

In this configuration the mobile devices are given static IP addresses and the FreeBSD bridge is pre-configured with 2 dummynet pipes for each IP address (one for inbound and one for outbound traffic).  The root account needs to allow for cert-based ssh access from the test machine (with the cert installed in authorized_keys).

Passing the agent a "--shaper external" command-line flag you give it the IP address of the FreeBSD bridge as well as the pipe numbers for inbound and outbound traffic for the device.  At test time the agent will ssh to the bridge and adjust the settings for the device as needed.

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

### Windows
* Make sure to install the 64-bit Python, otherwise it may not find 64-bit browser installs.
* Disable secure boot in the bios if enabled, otherwise traffic-shaping will not be available.
* Disable UAC so it doesn't prompt when admin commands need to be run
* Make sure all of the security zones in IE have the same setting for the "protected mode" checkbox (all enabled or all disabled)
* Consider using [browser-install](https://github.com/WPO-Foundation/browser-install) to keep the browsers up to date
* Running the agent from a batch file configured to start at user login (task scheduler) is a good way to run a headless agent.  Here is the batch file the public instance uses (reboots the system daily):
```bat
@echo off
cd C:\Users\WebPageTest\browser-install
git pull origin master
python.exe C:\Users\WebPageTest\browser-install\browser_install.py -vvvv --all
cd C:\Users\WebPageTest\wptagent
call npm i -g lighthouse
FOR /L %%x IN (1, 1, 24) DO (
git pull origin master
python.exe C:\Users\WebPageTest\wptagent\wptagent.py -vvvv --server "http://www.example.com/work/" --location Location_ID --key Location_key --exit 60
)
shutdown /r /f
```

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