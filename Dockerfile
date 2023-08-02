### IMPORTANT DOCKER COMMANDS ###

###     docker images                               - List images available
###     docker build <GITHUB-REPO-LINK> -t TAGNAME  - Builds the Dockerfile from the github repo
###     docker ps                                   - List running images
###     docker stop <IMAGE ID || IMAGE NAME>        - Stops running image with either --name <IMAGE NAME> || IMAGE ID>
###     docker run -it -d TAGNAME /bin/bash         - Runs bash
###     docker exec -it <IMAGE ID> /bin/bash        - Connects to bash for terminal execution (Needs to be running first)

### INSTALLING METHOD ###

###     Recommend to install with "docker build <GITHUB-REPO-LINK> -t TAGNAME",
###     grabs the latest copy of WPT and build time on average takes 10 minutes. 

FROM ubuntu:22.04 as production

### TIMEZONE INSIDE THE CONTAINER ###
ARG TIMEZONE=UTC

### UPDATE ###
RUN curl -sL https://deb.nodesource.com/setup_16.x | bash -
RUN apt update 

### INSTALL APT-GET LIBS ###
# DEBIAN_FRONTEND prevents interactive prompts while installing
# set default timezone beforehand to avoid user interaction for tzdata package
RUN ln -fs /usr/share/zoneinfo/$TIMEZONE /etc/localtime && DEBIAN_FRONTEND=noninteractive apt install -y \
    python3 python3-pip python3-ujson \
    imagemagick dbus-x11 traceroute software-properties-common psmisc libnss3-tools iproute2 net-tools openvpn \
    libtiff5-dev libjpeg-dev zlib1g-dev libfreetype6-dev liblcms2-dev libwebp-dev tcl8.6-dev tk8.6-dev python3-tk \
    python3-dev libavutil-dev libmp3lame-dev libx264-dev yasm autoconf automake build-essential libass-dev libfreetype6-dev libtheora-dev \
    libtool libvorbis-dev pkg-config texi2html libtext-unidecode-perl python3-numpy python3-scipy perl \
    adb ethtool nodejs cmake git-core libsdl2-dev libva-dev libvdpau-dev libxcb1-dev libxcb-shm0-dev libxcb-xfixes0-dev texinfo wget \
    ttf-mscorefonts-installer fonts-noto fonts-roboto fonts-open-sans ffmpeg npm sudo curl xvfb

### UPDATE FONT CACHE ###
RUN fc-cache -f -v

### INSTALLING LIGHTHOUSE FROM NPM ###
RUN npm install -g lighthouse

### INSTALLING CHROME BROWSER ###
RUN curl -o /tmp/google-chrome-stable_current_amd64.deb  https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && \
    apt install -y /tmp/google-chrome-stable_current_amd64.deb && rm /tmp/google-chrome-stable_current_amd64.deb

### UPGRADING PIP AND INSTALLING REQUIRED PACKAGES ###
COPY /.github/workflows/requirements.txt /tmp/agent_requirements.txt
RUN python3 -m pip install --upgrade --user pip && \
    python3 -m pip install --user -r /tmp/agent_requirements.txt && \
    rm /tmp/agent_requirements.txt

### COPYING ENTIRE DIR TO LOCAL DOCKER /wptagent ###
# see .dockerignore for filterd out folders
# source copy last so we don't need to rebuild all the other layers 
COPY / /wptagent
WORKDIR /wptagent

ENTRYPOINT ["/bin/sh", "/wptagent/docker/linux-headless/entrypoint.sh"]

### DEBUG CONTAINER ###
FROM production as debug

### INSTALLING DEBUG DEPENDENCIES ###
RUN pip install debugpy

### COPY DEBUG AGENT AND MOVE REAL ONE ###
RUN mv wptagent.py wptagent_starter.py
COPY wptagent_debug.py wptagent.py

### SETTING PRODUCTION BUILD AS DEFAULT ###
FROM production