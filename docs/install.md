# Requirements
wptagent currently supports Windows and Linux hosts (possibly OSX but not tested).  If traffic-shaping is enabled the agent will need to be able to elevate to admin/root. It is recommended that the agent itself not run as admin/root but that it can elevate without prompting which means disabling UAC on windows or adding the user account to the sudoers file on Linux.

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
* Debian:
    * ```sudo apt-get install -y python2.7 python-pip imagemagick ffmpeg xvfb cgroup-tools && sudo pip install dnspython monotonic pillow psutil requests ujson xvfbwrapper marionette_driver```
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

## OS Tweaks
### Linux
* By default Linux will take 1-2 minutes to time out on socket connections.  You can lower it to 20 seconds to fail faster (and match windows) by configuring the retries in /etc/sysctl.conf:
    * ```net.ipv4.tcp_syn_retries = 4```

## For lighthouse testing (coming soon)
* NodeJS 7.x
    * Ubuntu/Debian:
        * ```curl -sL https://deb.nodesource.com/setup_7.x | sudo bash -```
        * ```sudo apt-get install -y nodejs```
* The lighthouse npm module
    * ```sudo npm install -g lighthouse```

## Configuration
The default browser locations for Chrome and Firefox (Stable, Beta and Canary/Nightly) will automatically be detected.  If you need to support different locations or provide different browsers you can rename browsers.ini.sample to browsers.ini and define the browser locations.

On Linux, make sure to point to the actual chrome binary and not the symlink.  Usually something like:
```
[Chrome]
exe=/opt/google/chrome/chrome
```