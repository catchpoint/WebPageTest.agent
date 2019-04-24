#!/bin/bash

# Watchdog helper script to auto-reboot the system if the agent stops working.
# Add a test-binary entry to /etc/watchdog.conf and start the watchdog service:
# test-binary = /home/ubuntu/wptagent/alive.sh

DIR="$(cd "$(dirname "$0")" && pwd)"
rm /var/log/watchdog/* || true
python $DIR/alive.py --file /tmp/wptagent
