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
    * xvfbwrapper (Linux only)
    * bind9utils (Linux only, for rndc)
    * marionette_driver (Firefox)
* Imagemagick installed and available in the path
    * The legacy tools (convert, compare, etc) need to be installed which may be optional on Windows
* ffmpeg installed and available in the path
* Xvfb (Linux only)
* cgroup-tools (Linux only if mobile CPU emulation is desired)
* traceroute (Mac and Linux)
* Debian:
    * ```sudo apt-get install -y python2.7 python-pip imagemagick ffmpeg xvfb dbus-x11 cgroup-tools traceroute && sudo pip install dnspython monotonic pillow psutil requests ujson xvfbwrapper marionette_driver```
* Chrome Browser
    * Linux stable and unstable channels on Ubuntu/Debian:
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

## OS Tweaks
### Linux
* There are time when the default file handle limits are too small (particularly when testing Firefox).  Upping the limits in /etc/security/limits.conf (at the end of the file) can help:
    * ```* soft nofile 250000```
    * ```* hard nofile 300000```
* By default Linux will take 1-2 minutes to time out on socket connections.  You can lower it to 20 seconds to fail faster (and match windows) by configuring the retries in /etc/sysctl.conf:
    * ```net.ipv4.tcp_syn_retries = 4```

## For lighthouse testing
* NodeJS 7.x
    * Ubuntu/Debian:
        * ```curl -sL https://deb.nodesource.com/setup_7.x | sudo bash -```
        * ```sudo apt-get install -y nodejs```
* The lighthouse npm module
    * ```sudo npm install -g lighthouse```

## Remote traffic-shaping
wptagent supports configuring an external FreeBSD network bridge for traffic-shaping.  This is particularly useful for mobile device testing where the devices can be connected to a WiFi access point and the access point is connected through a FreeBSD bridge to get to the network.

i.e. mobile phone <--> WiFi <--> FreeBSD bridge <--> Internet

In this configuration the mobile devices are given static IP addresses and the FreeBSD bridge is pre-configured with 2 dummynet pipes for each IP address (one for inbound and one for outbound traffic).  The root account needs to allow for cert-based ssh access from the test machine (with the cert installed in authorized_keys).

Passing the agent a "--shaper external" command-line flag you give it the IP address of the FreeBSD bridge as well as the pipe numbers for inbound and outbound traffic for the device.  At test time the agent will ssh to the bridge and adjust the settings for the device as needed.

## iOS testing
iOS testing requires an iOS device running [iWptBrowser](https://github.com/WPO-Foundation/iWptBrowser/blob/master/docs/walkthrough.md) (a Safari shell), the --iOS command-line flag and is supported on Mac as well as Linux hosts for controlling the device (a Raspberry Pi is recommended).

Local traffic-shaping isn't supported so either "none" or "external" needs to be specified for --shaper.

On Linux the usbmuxd support libraries should be installed automatically but if there are problems talking to the device you may need to install them manually.  To verify connectivity to the device you can run "ideviceinfo" from the command-line to make sure things are working.

## Configuration
The default browser locations for Chrome, Firefox (Stable, Beta and Canary/Nightly) and Microsoft Edge will automatically be detected.  If you need to support different locations or provide different browsers you can rename browsers.ini.sample to browsers.ini and define the browser locations.

On Linux, make sure to point to the actual chrome binary and not the symlink.  Usually something like:
```
[Chrome]
exe=/opt/google/chrome/chrome
```