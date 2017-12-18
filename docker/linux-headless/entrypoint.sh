#!/bin/bash
set -e

# exec replaces the shell process by the python process and is required to
# propagate signals (i.e. SIGTERM)
exec python /wptagent/wptagent.py --xvfb --dockerized $@
