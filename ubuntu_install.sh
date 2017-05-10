#!/bin/bash
sudo apt-get install -y python-pip imagemagick ffmpeg xvfb
sudo pip install dnspython monotonic pillow psutil requests ujson xvfbwrapper cgroup-tools
curl -sL https://deb.nodesource.com/setup_7.x | sudo bash -
sudo apt-get install -y nodejs
sudo npm install -g lighthouse
sudo npm update -g
wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
sudo sh -c 'echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list' 
sudo apt-get update
sudo apt-get install -y google-chrome-stable google-chrome-beta google-chrome-unstable

