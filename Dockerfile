# FROM debian:jessie-slim
FROM ubuntu:18.04

RUN apt-get update && \
  apt-get install -y \
    wget \
    curl \
    python \
    python-pip \
    xvfb \
    imagemagick \
    python-dev \
    zlib1g-dev \
    libjpeg-dev \
    psmisc \
    dbus-x11 \
    sudo \
    kmod \
    ffmpeg \
    net-tools \
    tcpdump \
    traceroute \
    bind9utils \
    libnss3-tools \
    iproute2 \
    software-properties-common && \
# Node setup
  curl -sL https://deb.nodesource.com/setup_10.x | sudo -E bash - && \
  wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - && \
  wget -qO- https://deb.opera.com/archive.key | apt-key add - && \
  echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list && \
# Set repos
  add-apt-repository -y ppa:ubuntu-mozilla-daily/ppa && \
  add-apt-repository -y 'deb https://deb.opera.com/opera-stable/ stable non-free' && \
  add-apt-repository -y 'deb https://deb.opera.com/opera-beta/ stable non-free' && \
  add-apt-repository -y 'deb https://deb.opera.com/opera-developer/ stable non-free' && \
# Install browsers
  apt-get update && \
  DEBIAN_FRONTEND=noninteractive apt-get install -yq \
  google-chrome-stable \
  google-chrome-beta \
  google-chrome-unstable \
  firefox \
  firefox-trunk \
  opera-stable \
  opera-beta \
  opera-developer \
  nodejs && \
# Get fonts
  echo ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true | sudo debconf-set-selections && \
  sudo DEBIAN_FRONTEND=noninteractive apt-get -y install ttf-mscorefonts-installer fonts-noto* && \
  sudo fc-cache -f -v && \
# Cleaup to save space in layer
  sudo apt-get clean && \
# Install lighthouse
  npm install -g lighthouse && \
# Install other utilities
  pip install \
    dnspython \
    monotonic \
    pillow \
    psutil \
    requests \
    ujson \
    tornado \
    wsaccel \
    xvfbwrapper \
    marionette_driver

COPY wptagent.py /wptagent/wptagent.py
COPY internal /wptagent/internal
COPY ws4py /wptagent/ws4py
COPY docker/linux-headless/entrypoint.sh /wptagent/entrypoint.sh

WORKDIR /wptagent

CMD ["/bin/bash", "/wptagent/entrypoint.sh"]
