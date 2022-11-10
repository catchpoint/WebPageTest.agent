### IMPORTANT DOCKER COMMANDS ###

###     docker images                               - List images available
###     docker build <GITHUB-REPO-LINK> -t TAGNAME  - Builds the Dockerfile from the github repo
###     docker ps                                   - List running images
###     docker stop <IMAGE ID || IMAGE NAME>        - Stops running image with either --name <IMAGE NAME> || IMAGE ID>
###     docker run -it -d TAGNAME /bin/bash         - Runs bash
###     docker exec -it <IMAGE ID> /bin/bash        - Connects to bash for terminal execution (Needs to be running first)

### EXAMPLE DOCKER COMMANDS FOR RUNNING SERVER & AGENT

###     docker run -d -p 4000:80 <IMAGE ID || <IMAGE TAG>
###     docker run -d -p 4001:80 --network="host" -e "SERVER_URL=http://localhost:4000/work/" -e "LOCATION=Test" -e "-v" <IMAGE ID || <IMAGE TAG>

### INSTALLING METHOD ###

###     Recommend to install with "docker build <GITHUB-REPO-LINK> -t TAGNAME",
###     grabs the latest copy of WPT and build time on average takes 10 minutes. 

FROM ubuntu

### PREVENTs INTERACTIVE PROMPTS WHILE INSTALLING ###
ARG DEBIAN_FRONTEND=noninteractive

### COPYING ENTIRE DIR TO LOCAL DOCKER /wptagent
COPY / /wptagent
RUN apt-get update

# Git Clone Install
# RUN apt-get install -y git
# RUN git clone -b dockerfile https://github.com/sammeboy635/wptagent.git

### UPDATE ###
RUN apt-get update 

### INSTALL APT-GET LIBS ###
RUN xargs -a /wptagent/.github/workflows/docker-apt-get.txt apt-get install --no-install-recommends --yes; exit 0

### UPGRADING PIP AND INSTALLING REQUIRED PACKAGES ###
RUN python3 -m pip install --upgrade --user pip && \
    python3 -m pip install --user -r /wptagent/.github/workflows/requirements.txt 

### INSTALLING LIGHTHOUSE FROM NPM ###
RUN npm install -g lighthouse

### INSTALLING CHROME BROWSER ###
###     Fails to Find all libs needed to run
# RUN wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && \
#     dpkg -i google-chrome-stable_current_amd64.deb; exit 0 && \
#     apt -f install -y && \
#     apt-get install google-chrome-stable

### BETTER INSTALLING CHROME BROWSER METHOD ###
###     Better Installing method but would like to change this to something less complex.
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list
RUN apt-get update && apt-get -y install google-chrome-stable ; exit 0
RUN apt-get update --fix-missing -y
RUN apt-get install -f -y

### CLEAN UP ###
#       We could add some clean up here but in testing it was negotiable


WORKDIR /wptagent

### /bin/bash LOCATION OF COMMAND EXECUTION ###
CMD ["/bin/bash", "/wptagent/docker/linux-headless/entrypoint.sh"]
