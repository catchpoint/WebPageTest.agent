# FROM debian:jessie-slim
FROM ubuntu

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
    software-properties-common \
    python-software-properties

RUN curl -sL https://deb.nodesource.com/setup_7.x | bash - && \
  wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - && \
  wget -qO- https://deb.opera.com/archive.key | apt-key add - && \
  echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list

RUN add-apt-repository -y ppa:ubuntu-mozilla-daily/ppa && \
  add-apt-repository -y 'deb https://deb.opera.com/opera-stable/ stable non-free' && \
  add-apt-repository -y 'deb https://deb.opera.com/opera-beta/ stable non-free' && \
  add-apt-repository -y 'deb https://deb.opera.com/opera-developer/ stable non-free'

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -yq \
    google-chrome-stable \
    google-chrome-beta \
    google-chrome-unstable \
    firefox \
    firefox-trunk \
    opera-stable \
    opera-beta \
    opera-developer \
    nodejs

RUN npm install -g lighthouse

RUN pip install \
    dnspython \
    monotonic \
    pillow \
    psutil \
    requests \
    ujson \
    tornado \
    xvfbwrapper \
    marionette_driver

COPY wptagent.py /wptagent/wptagent.py
COPY internal /wptagent/internal
COPY ws4py /wptagent/ws4py
COPY docker/linux-headless/entrypoint.sh /wptagent/entrypoint.sh

WORKDIR /wptagent

CMD ["/bin/bash", "/wptagent/entrypoint.sh"]
