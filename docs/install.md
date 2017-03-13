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
    * websocket-client
    * xvfbwrapper (Linux only)
* Imagemagick installed and available in the path
    * The legacy tools (convert, compare, etc) need to be installed which may be optional on Windows
* ffmpeg installed and available in the path
* Xvfb (Linux only)
* Chrome Browser
    * Linux stable channel on Ubuntu/Debian:
        * ```wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | sudo apt-key add -```
        * ```sudo sh -c 'echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list'```
        * ```sudo apt-get update```
        * ```sudo apt-get install -y google-chrome-stable```


## For lighthouse testing (coming soon)
* NodeJS 7.x
    * Ubuntu/Debian:
        * ```curl -sL https://deb.nodesource.com/setup_7.x | sudo bash -```
        * ```sudo apt-get install -y nodejs```
* The lighthouse npm module
    * ```sudo npm install -g lighthouse```

## Configuration
Once all of the dependencies are installed, the only configuration required is to rename browsers.ini.sample to browsers.ini and configure it to point to the path to the browser to be tested for each configuration.