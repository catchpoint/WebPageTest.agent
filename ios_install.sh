#!/bin/bash
cd ~
# Pre-prompt for the sudo authorization so it doesn't prompt later
sudo date

# Build ios-webkit-debug-proxy (and dependencies) from source
until sudo apt -y update
do
    sleep 1
done
until sudo DEBIAN_FRONTEND=noninteractive apt -yq install build-essential \
cmake python-dev cython swig automake autoconf libtool libusb-1.0-0 libusb-1.0-0-dev \
libreadline-dev openssl libssl-dev
do
    sleep 1
done
sudo DEBIAN_FRONTEND=noninteractive apt -yq install libssl1.0.2 
sudo DEBIAN_FRONTEND=noninteractive apt -yq install libssl1.1 
cd ~

git clone --depth 1 https://github.com/libimobiledevice/libplist.git libplist
cd libplist
./autogen.sh
make
sudo make install
cd ~
rm -rf libplist

git clone --depth 1 https://github.com/libimobiledevice/libusbmuxd.git libusbmuxd
cd libusbmuxd
./autogen.sh
make
sudo make install
cd ~
rm -rf libusbmuxd

git clone --depth 1 https://github.com/libimobiledevice/libimobiledevice.git libimobiledevice
cd libimobiledevice
./autogen.sh
make
sudo make install
cd ~
rm -rf libimobiledevice

git clone --depth 1 https://github.com/libimobiledevice/usbmuxd.git usbmuxd
cd usbmuxd
./autogen.sh
make
sudo make install
cd ~
rm -rf usbmuxd

git clone --depth 1 https://github.com/google/ios-webkit-debug-proxy.git ios-webkit-debug-proxy
cd ios-webkit-debug-proxy
./autogen.sh
make
sudo make install
cd ~
rm -rf ios-webkit-debug-proxy

sudo sh -c 'echo /usr/local/lib > /etc/ld.so.conf.d/libimobiledevice-libs.conf'
sudo ldconfig