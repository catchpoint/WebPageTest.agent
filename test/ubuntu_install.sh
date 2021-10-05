#!/usr/bin/env bash

# Test script for ubuntu_install.sh

set -eux

# Check if packages are installed
dpkg --status python2.7 > /dev/null
dpkg --status python-pip > /dev/null
dpkg --status imagemagick > /dev/null
dpkg --status ffmpeg > /dev/null
dpkg --status xvfb > /dev/null
dpkg --status python2.7 > /dev/null
dpkg --status dbus-x11 > /dev/null
dpkg --status cgroup-tools > /dev/null
dpkg --status traceroute > /dev/null
dpkg --status software-properties-common > /dev/null
dpkg --status psmisc > /dev/null
dpkg --status libnss3-tools > /dev/null
dpkg --status iproute2 > /dev/null
dpkg --status net-tools > /dev/null
dpkg --status git > /dev/null
dpkg --status curl > /dev/null
dpkg --status nodejs > /dev/null
dpkg --status google-chrome-stable > /dev/null
dpkg --status google-chrome-beta > /dev/null
dpkg --status google-chrome-unstable > /dev/null
dpkg --status firefox > /dev/null
dpkg --status firefox-trunk > /dev/null
dpkg --status firefox-esr > /dev/null
dpkg --status ttf-mscorefonts-installer > /dev/null
dpkg --status fonts-noto > /dev/null
dpkg --status fonts-noto-cjk > /dev/null
dpkg --status fonts-noto-cjk-extra > /dev/null
dpkg --status fonts-noto-color-emoji > /dev/null
dpkg --status fonts-noto-hinted > /dev/null
dpkg --status fonts-noto-mono > /dev/null
dpkg --status fonts-noto-unhinted > /dev/null

# TODO Following is commented out temporary.
# The max number of open file is currently configured in /etc/security/limits.conf,
# but it is ignored on systemd-based systems.

# # Soft limit of max number of open files
# test "$(ulimit -Sn)" == "250000"
# # Hard limit of max number of open files
# test "$(ulimit -Hn)" == "300000"

sudo sysctl --system
test "$(sysctl net.ipv4.tcp_syn_retries)" == "net.ipv4.tcp_syn_retries = 4"
