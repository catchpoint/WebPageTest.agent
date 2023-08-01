#!/bin/bash
if [ -z "$SERVER_URL" ]; then
  echo >&2 'SERVER_URL not set'
  exit 1
fi

if [ -z "$LOCATION" ]; then
  echo >&2 'LOCATION not set'
  exit 1
fi

# exec replaces the shell process by the python process and is required to
# propagate signals (i.e. SIGTERM)
exec python3 /wptagent/wptagent.py --server "${SERVER_URL}" --location "${LOCATION}" --xvfb --dockerized "$@"
