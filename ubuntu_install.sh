#!/bin/bash
sudo apt-get install -y python2.7 python-pip imagemagick ffmpeg xvfb cgroup-tools software-properties-common python-software-properties
sudo pip install dnspython monotonic pillow psutil requests ujson xvfbwrapper marionette_driver
curl -sL https://deb.nodesource.com/setup_7.x | sudo bash -
sudo apt-get install -y nodejs
sudo npm install -g lighthouse
sudo npm update -g
wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
sudo sh -c 'echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list' 
sudo add-apt-repository -y ppa:ubuntu-mozilla-daily/ppa
sudo apt-get update
sudo apt-get install -y google-chrome-stable google-chrome-beta google-chrome-unstable firefox firefox-trunk

