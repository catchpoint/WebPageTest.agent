#!/bin/bash
until sudo yum -y update
do
    sleep 1
done
until sudo yum -y upgrade
do
    sleep 1
done
until sudo yum install -y epel-release
do
    sleep 1
done
sudo rpm --import http://li.nux.ro/download/nux/RPM-GPG-KEY-nux.ro
sudo rpm -Uvh http://li.nux.ro/download/nux/dextop/el7/x86_64/nux-dextop-release-0-5.el7.nux.noarch.rpm
until sudo yum groupinstall -y development
do
    sleep 1
done
until sudo yum install -y gcc-c++ python3 python3-devel ImageMagick ffmpeg Xvfb dbus-x11 libcgroup libcgroup-tools traceroute tcpdump psmisc python-devel
do
    sleep 1
done
sudo dbus-uuidgen --ensure
python3 -m pip install --upgrade --user pip
until python3 -m pip install --user -r .github/workflows/requirements.txt 
do
    sleep 1
done
curl --silent --location https://rpm.nodesource.com/setup_16.x | sudo bash -
until sudo yum -y install nodejs
do
    sleep 1
done
sudo npm install -g lighthouse
sudo npm update -g
echo "[google-chrome]" | sudo tee /etc/yum.repos.d/google-chrome.repo
echo "name=google-chrome" | sudo tee -a /etc/yum.repos.d/google-chrome.repo
echo "baseurl=http://dl.google.com/linux/chrome/rpm/stable/x86_64" | sudo tee -a /etc/yum.repos.d/google-chrome.repo
echo "enabled=1" | sudo tee -a /etc/yum.repos.d/google-chrome.repo
echo "gpgcheck=1" | sudo tee -a /etc/yum.repos.d/google-chrome.repo
echo "gpgkey=https://www.webpagetest.org/keys/google/linux_signing_key.pub" | sudo tee -a /etc/yum.repos.d/google-chrome.repo
until sudo yum -y install google-chrome-stable google-chrome-beta google-chrome-unstable firefox
do
    sleep 1
done
echo '# Limits increased for wptagent' | sudo tee -a /etc/security/limits.conf
echo '* soft nofile 250000' | sudo tee -a /etc/security/limits.conf
echo '* hard nofile 300000' | sudo tee -a /etc/security/limits.conf
echo '# wptagent end' | sudo tee -a /etc/security/limits.conf
echo 'net.ipv4.tcp_syn_retries = 4' | sudo tee -a /etc/sysctl.d/50-wptagent.conf
sudo fc-cache -f -v
sudo sysctl -p
echo 'Reboot is recommended before starting testing'

# Running command used to test
# python3 wptagent.py -vvvv --xvfb --dockerized --testurl www.google.com

