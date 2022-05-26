#!/bin/bash

set -eu

: ${UBUNTU_VERSION:=`(lsb_release -rs | cut -b 1,2)`}

until sudo apt-get update
do
    sleep 1
done
if [ "$UBUNTU_VERSION" \< "20" ]; then
    until sudo apt-get install -y python2.7 python-pip python-ujson python-xlib
    do
        sleep 1
    done
else
    until sudo apt-get install -y python python3 python3-pip python3-ujson python3-xlib
    do
        sleep 1
    done
fi
until sudo apt-get install -y imagemagick ffmpeg xvfb dbus-x11 cgroup-tools traceroute software-properties-common psmisc libnss3-tools iproute2 net-tools git curl
do
    sleep 1
done
# Unavailable on Ubuntu 18.04 but needed on earlier releases
if [ "$UBUNTU_VERSION" \< "18" ]; then
    sudo apt-get install -y python-software-properties || :
fi
sudo dbus-uuidgen --ensure
if [ "$UBUNTU_VERSION" \< "20" ]; then
    until sudo pip install dnspython monotonic pillow psutil requests tornado wsaccel xvfbwrapper marionette_driver selenium future usbmuxwrapper
    do
        sleep 1
    done
    sudo pip install 'fonttools>=3.44.0,<4.0.0'
else
    until sudo pip3 install dnspython monotonic pillow psutil requests tornado wsaccel xvfbwrapper selenium future usbmuxwrapper
    do
        sleep 1
    done
    sudo pip3 install 'fonttools>=3.44.0,<4.0.0'
fi
curl -sL https://deb.nodesource.com/setup_16.x | sudo -E bash -
until sudo apt-get install -y nodejs
do
    sleep 1
done
until sudo npm install -g lighthouse
do
    sleep 1
done
sudo npm update -g
wget -q -O - https://www.webpagetest.org/keys/google/linux_signing_key.pub | sudo apt-key add -
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
