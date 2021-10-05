#!/bin/bash
wget -qO- https://www.webpagetest.org/keys/opera/archive.key | sudo apt-key add -
sudo add-apt-repository -y 'deb https://deb.opera.com/opera-stable/ stable non-free'
sudo add-apt-repository -y 'deb https://deb.opera.com/opera-beta/ stable non-free'
sudo add-apt-repository -y 'deb https://deb.opera.com/opera-developer/ stable non-free'
sudo apt-get update
until sudo DEBIAN_FRONTEND=noninteractive apt-get install -yq opera-stable opera-beta opera-developer
do
    sleep 1
done
