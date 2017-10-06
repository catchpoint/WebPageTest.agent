# Walkthrough for configuring an agent on Google Compute Engine (GCE)
Since GCE images are not publicly shareable it is necessary to configure a new image for each project.  This isn't the only (or even necessarily the best) way to do it but here is a walkthrough of the steps I use to create a new GCE image for testing Chrome and Firefox on Linux on GCE.

## Start with a working WebPageTest server
* Get a WebPageTest server configured and a location set up so you can test the instance before making an image from it.
* Keep track of the server hostname, location ID and location key for the configured test location
* Configure it with the following browser list in locations.ini: 
    * ```browser=Chrome,Chrome Beta,Chrome Canary,Firefox,Firefox Nightly```

## Launch a base instance
* Start at the Google Compute Engine console for the project you are creating the image for:
    * [https://console.cloud.google.com/compute/instances](https://console.cloud.google.com/compute/instances)
* Create an instance
    * Name it something that you will recognize in the list (name doesn't matter)
    * Select a region near you.  Once created the image can be deployed globally but selecting a region near you for configuration makes setup easier.
    * Select a 1 vCPU machine (the default - n1-standard-1)
        * The image can be deployed to any instance size after being configured though the shared CPU instances will have inconsistent performance and are not recommended.
    * Change the boot disk to "Ubuntu 16.04 LTS" from the "OS Images" list.
        * Leave the boot disk type as "standard persistent disk" and size at 10GB
    * Expand the "Management, disks, networking, SSH keys" section
    * In the management tab, add the test location configuration to the metadata
        * The key should be ```wpt_data```
        * The value should include the the test location information
            * ```wpt_server=<host> wpt_loc=<locationID> wpt_key=<locationKey>```
            * i.e. ```wpt_server=www.webpagetest.org wpt_loc=Test wpt_key=XXYYZZ```
        * When launching new instances in various regions this is how the image will be dynamically configured to connect to the right server location (no need to modify the image itself once created)
    * Leave preemptability off when creating the image but feel free to use preemptable images for actual testing to reduce costs (possibly combined with Managed Instance Groups)
    * In the disks tab, uncheck "Delete boot disk when instance is deleted".
    * In the SSH Keys tab, add a public key for the "ubuntu" user (using a public key that you have access to the private key for ssh for - usually ~/.ssh/id_rsa.pub).
        * Make SURE it detects the user as ```ubuntu``` to the left of the key after pasting (change the user name at the end of the string if necessary):
            * i.e. ```ssh-rsa AAAAB3NzaC1yc2EAAAABJQAAAQEAgQFEo04ebO4BhG/1p2TryUA5GLhyCmyOkilDLha1EWkE0VIPqO7/Ezwk3vrRjPbHohxWmvX41+1AlUCmeh71iMuj838UYy69ombks+VCodufJ6KBzBexZ6lyjJsv4baCAi72RB2Sr6cVVoh020iOcwhMd5dK87gMgLzx1asyBSDNUPPaPQsqmqoA6p+hxhVvPr+iWVVKISSI8Sb0nQ127vIjYJMrSZxitCzieIUcNKLx7uqgwq52BxJwWV64R3fI1y0+OIx+/M1fQ3qUGVavvBNAKAAe1jJtSibYy/DO2L5rDMh39EX+uCDoK1gu7xlVnLLvVNwezTPE2LGsbkADHw== ubuntu```
    * Click create

## Install the software
* SSH into the newly-created instance as the user "ubuntu" with the SSH key you provided (using the "External IP" for the instance)
    * ```sss ubuntu@1.2.3.4```
* Clone the wptagent code
    * ```git clone https://github.com/WPO-Foundation/wptagent.git```
* Run the Ubuntu install script
    * ```wptagent/ubuntu_install.sh```
    * This will install all of the dependencies (including Lighthouse), Chrome and Firefox
* Install any OS updates
    * ```sudo apt-get update && sudo apt-get -y dist-upgrade && sudo apt-get -y autoremove```
* Create a shell script to run the agent
    * This script installs all OS and browser updates at startup (before running the agent) and every 24 hours after that.  It also updates the agent code from Github hourly (modify it if you don't want to auto-update):
    * ```nano ~/agent.sh```
    * Paste the following script (ctrl-O to save, ctrl-X to exit after you're done):
```sh
#!/bin/sh
cd ~/wptagent
echo "Waiting for 30 second startup delay"
sleep 30
echo "Waiting for apt to become available"
while fuser /var/lib/dpkg/lock >/dev/null 2>&1 ; do
    sleep 1
done
while :
do
    echo "Updating OS"
    until sudo timeout 20m apt-get update
    do
        sleep 1
    done
    until sudo DEBIAN_FRONTEND=noninteractive apt-get -yq -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" dist-upgrade
    do
        sleep 1
    done
    sudo apt-get -y autoremove
    sudo npm i -g lighthouse
    sudo npm -g outdated --parseable=true | cut -d : -f 4 | xargs -n 1 sudo npm -g install
    for i in `seq 1 24`
    do
        timeout 10m git pull origin master
        python wptagent.py -vvvv --gce --xvfb --fps 30 --throttle --exit 60 --alive /tmp/wptagent
        echo "Exited, restarting"
        sleep 1
    done
done
```
* Create a shell script to launch the agent in a detached screen (makes it easy to ssh into an agent and ```screen -r``` to watch the agent activity as it runs
    * ```nano ~/startup.sh```
    * Paste the following script:
```sh
#!/bin/sh
PATH=/home/ubuntu/bin:/home/ubuntu/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin
screen -dmS agent ~/agent.sh
```
* Make the scripts executable
    * ```chmod +x ~/*.sh```
* Configure the startup script to run at boot time
    * ```crontab -e```
    * Add the following to the end of the cron file:
        * ```@reboot /home/ubuntu/startup.sh```

## Test the agent
* Reboot the vm ```sudo reboot```
* Submit some tests to the web UI for the test location and make sure tests run as expected (if not, ssh into the VM and connect to the screen session to see what it is doing)

## Create the image
* ssh into the VM and shut it down ```sudo poweroff```
* Go back to the VM Instances display in the cloud console for GCE
* Select the instance
* Click the Delete button
* Make sure the message does not say it will also delete the disk (if it does, cancel, edit the instance to not delete the disk and go back to delete again)
* Click the delete button in the message
* Go to the "Images" Section
* Click "Create Image"
* Give it a name you will recognize (I recommend including the date in case you choose to update the image later)
* From the source disk dropdown, select the disk name that matches the instance you had set up
* Click Create
* Wait for the "Creating..." message to go away and for the UI to return to the image list
* Go to the "Disks" section
* Delete the disk that the image was created from (no longer needed)

## Profit