#!/bin/bash

set -eu

until sudo apt-get update
do
    sleep 1
done
until sudo apt-get install -y python2.7 python-pip imagemagick ffmpeg xvfb dbus-x11 cgroup-tools traceroute software-properties-common psmisc libnss3-tools iproute2 net-tools git curl
do
    sleep 1
done
# Unavailable on Ubuntu 18.04 but needed on earlier releases
sudo apt-get install -y python-software-properties || :
sudo dbus-uuidgen --ensure
until sudo pip install dnspython monotonic pillow psutil requests git+git://github.com/marshallpierce/ultrajson.git@v1.35-gentoo-fixes tornado wsaccel xvfbwrapper brotli marionette_driver selenium future
do
    sleep 1
done
sudo pip install 'fonttools>=3.44.0,<4.0.0'
curl -sL https://deb.nodesource.com/setup_12.x | sudo -E bash -
until sudo apt-get install -y nodejs
do
    sleep 1
done
until sudo npm install -g lighthouse
do
    sleep 1
done
sudo npm update -g
wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
sudo sh -c 'echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list'
sudo add-apt-repository -y ppa:ubuntu-mozilla-daily/ppa
sudo add-apt-repository -y ppa:mozillateam/ppa
sudo apt-get update
until sudo DEBIAN_FRONTEND=noninteractive apt-get install -yq google-chrome-stable google-chrome-beta google-chrome-unstable firefox firefox-trunk firefox-esr firefox-geckodriver
do
    sleep 1
done
echo ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true | sudo debconf-set-selections
until sudo DEBIAN_FRONTEND=noninteractive apt-get -y install ttf-mscorefonts-installer fonts-noto*
do
    sleep 1
done
sudo fc-cache -f -v
sudo apt-get clean

cat << _LIMITS_ | sudo tee /etc/security/limits.d/wptagent.conf
# Limits increased for wptagent
* soft nofile 250000
* hard nofile 300000
_LIMITS_

cat << _SYSCTL_ | sudo tee /etc/sysctl.d/60-wptagent.conf
net.ipv4.tcp_syn_retries = 4
_SYSCTL_

sudo sysctl -p

echo 'Reboot is recommended before starting testing'
