### IMPORTANT DOCKER COMMANDS ###
###     docker images                               - List images available
###     docker build <PATH> -t TAGNAME              - Builds the Dockerfile
###     docker ps                                   - List running containers
###     docker stop <CONTAINER ID || NAME>          - Stops running container
###     docker run -it -d TAGNAME /bin/bash         - Runs bash in detached mode
###     docker exec -it <CONTAINER ID> /bin/bash    - Connects to running container

### INSTALLING METHOD ###
###     Recommended to install with:
###     docker build . -t wpt-agent-debug:latest
###     Average build time: ~10 minutes

# ------------------------
# Base production image
# ------------------------
FROM ubuntu:22.04 as production

ARG TIMEZONE=UTC

# --- Update & install dependencies ---
RUN apt update && \
    ln -fs /usr/share/zoneinfo/$TIMEZONE /etc/localtime && \
    DEBIAN_FRONTEND=noninteractive apt install -y \
    python3 python3-pip python3-ujson \
    imagemagick dbus-x11 traceroute software-properties-common psmisc libnss3-tools iproute2 net-tools openvpn \
    libtiff5-dev libjpeg-dev zlib1g-dev libfreetype6-dev liblcms2-dev libwebp-dev tcl8.6-dev tk8.6-dev python3-tk \
    python3-dev libavutil-dev libmp3lame-dev libx264-dev yasm autoconf automake build-essential libass-dev libfreetype6-dev \
    libtheora-dev libtool libvorbis-dev pkg-config texi2html libtext-unidecode-perl python3-numpy python3-scipy perl \
    adb ethtool cmake git-core libsdl2-dev libva-dev libvdpau-dev libxcb1-dev libxcb-shm0-dev libxcb-xfixes0-dev texinfo wget \
    ttf-mscorefonts-installer fonts-noto fonts-roboto fonts-open-sans ffmpeg sudo curl xvfb gnupg ca-certificates \
    tini

# ------------------------
# Install Node.js (fixed)
# ------------------------
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g npm@latest

# ------------------------
# Update font cache
# ------------------------
RUN fc-cache -f -v

# ------------------------
# Install Lighthouse globally
# ------------------------
RUN npm install -g lighthouse

# ------------------------
# Install Google Chrome (stable)
# ------------------------
RUN curl -o /tmp/google-chrome-stable_current_amd64.deb \
    https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && \
    apt install -y /tmp/google-chrome-stable_current_amd64.deb && \
    rm /tmp/google-chrome-stable_current_amd64.deb

# ------------------------
# Upgrade pip and install requirements
# ------------------------
COPY /.github/workflows/requirements.txt /tmp/agent_requirements.txt
RUN python3 -m pip install --upgrade --user pip && \
    python3 -m pip install --user -r /tmp/agent_requirements.txt && \
    rm /tmp/agent_requirements.txt

# ------------------------
# Copy WPT Agent source
# ------------------------
COPY / /wptagent
WORKDIR /wptagent

# ------------------------
# Entrypoint for production mode (WITH TINI)
# ------------------------
ENTRYPOINT ["/usr/bin/tini", "--", "/bin/sh", "/wptagent/docker/linux-headless/entrypoint.sh"]

# ------------------------
# Debug build
# ------------------------
FROM production as debug

# Install debug helper
RUN pip install debugpy

# Replace main agent script with debug version
RUN mv wptagent.py wptagent_starter.py
COPY wptagent_debug.py wptagent.py

# Default to production build
FROM production
# FROM debug
