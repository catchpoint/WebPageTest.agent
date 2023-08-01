FROM ubuntu as production

# since we don't really need a stable base we can also upgrade
RUN apt update && apt upgrade -y

# Get files
# see .dockerignore for filterd out folders
COPY / /wptagent

# dependencies

## apt dependencies (took them from https://github.com/WPO-Foundation/wptagent/blob/master/.github/workflows/wptagent_test.yml#L31)
# split into 2 parts to show dependency gathered from docs and found during upgrade of the docker image
# set UTC as default timezone before end to avoid user interaction for tzdata package
RUN ln -fs /usr/share/zoneinfo/UTC /etc/localtime && \
    DEBIAN_FRONTEND=noninteractive apt install -y pylint apt-transport-https xserver-xorg-video-dummy xvfb gnupg2 python3-ujson imagemagick \
    dbus-x11 traceroute software-properties-common psmisc libnss3-tools iproute2 net-tools openvpn libtiff5-dev \
    libjpeg-dev zlib1g-dev libfreetype6-dev liblcms2-dev libwebp-dev tcl8.6-dev tk8.6-dev python3-tk python3-dev \
    libavutil-dev libmp3lame-dev libx264-dev yasm autoconf automake build-essential libass-dev libfreetype6-dev \
    libtheora-dev libtool libvorbis-dev pkg-config texi2html libtext-unidecode-perl python3-numpy python3-scipy \
    perl adb ethtool nodejs cmake libsdl2-dev libva-dev libvdpau-dev libxcb1-dev libxcb-shm0-dev libxcb-xfixes0-dev \
    texinfo wget ttf-mscorefonts-installer fonts-noto fonts-roboto fonts-open-sans
RUN apt install -y python3 python3-pip curl npm sudo


## python dependencies
# FIXME split requirements into dev requirements and release requirements
RUN pip install -r /wptagent/.github/workflows/requirements.txt && rm -rf /wptagent/.github/

## npm dependencies
RUN npm install -g lighthouse

# install chrome, simplified version
RUN curl -o google-chrome-stable_current_amd64.deb  https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && \
    apt install -y /google-chrome-stable_current_amd64.deb && rm /google-chrome-stable_current_amd64.deb

WORKDIR /wptagent

ENTRYPOINT ["/bin/sh", "/wptagent/docker/linux-headless/entrypoint.sh"]

# Create debug build that waits for a debugger to attach
FROM production as debug

RUN pip install debugpy

RUN mv wptagent.py wptagent_starter.py

COPY wptagent_debug.py wptagent.py

# set production build as default
FROM production