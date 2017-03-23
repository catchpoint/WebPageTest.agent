#!/bin/bash
sudo apt-get install -y python-pip imagemagick ffmpeg xvfb bind9utils
sudo pip install dnspython monotonic pillow psutil requests ujson websocket-client xvfbwrapper
wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
sudo sh -c 'echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list' 
sudo apt-get update
sudo apt-get install -y google-chrome-stable

