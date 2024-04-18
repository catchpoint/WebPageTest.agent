#!/bin/bash

set -eu

echo "If you are creating a dedicated agent, it is highly recommended to use the wptagent-install script"
echo "https://github.com/WPO-Foundation/wptagent-install"
echo
read -p "Press enter to continue (or ctrl-c to exit)"

until sudo apt-get update
do
    sleep 1
done

# Prepare Node for install
curl -sL https://deb.nodesource.com/setup_16.x | sudo -E bash -

# Install all of the binary dependencies
echo ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true | sudo debconf-set-selections
until sudo apt -y install git curl wget apt-transport-https gnupg2 python3 python3-pip python3-ujson \
        imagemagick dbus-x11 traceroute software-properties-common psmisc libnss3-tools iproute2 net-tools openvpn \
        libtiff5-dev libjpeg-dev zlib1g-dev libfreetype6-dev liblcms2-dev libwebp-dev tcl8.6-dev tk8.6-dev python3-tk \
        python3-dev libavutil-dev libmp3lame-dev libx264-dev yasm autoconf automake build-essential libass-dev libfreetype6-dev libtheora-dev \
        libtool libvorbis-dev pkg-config texi2html libtext-unidecode-perl python3-numpy python3-scipy \
        adb ethtool nodejs cmake git-core libsdl2-dev libva-dev libvdpau-dev libxcb1-dev libxcb-shm0-dev libxcb-xfixes0-dev texinfo wget \
        ttf-mscorefonts-installer fonts-noto fonts-roboto fonts-open-sans

sudo dbus-uuidgen --ensure
sudo fc-cache -f -v

# Install the python modules
until sudo pip3 install dnspython monotonic pillow psutil requests tornado wsaccel brotli fonttools selenium future usbmuxwrapper pytz tzlocal
do
    sleep 1
done

# Lighthouse
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
