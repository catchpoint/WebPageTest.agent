#!/bin/bash
set -e

if [ -z "$SERVER_URL" ]; then
  echo >&2 'SERVER_URL not set'
  exit 1
fi

if [ -z "$LOCATION" ]; then
  echo >&2 'LOCATION not set'
  exit 1
fi

EXTRA_ARGS=""

if [ -n "$NAME" ]; then
  EXTRA_ARGS="$EXTRA_ARGS --name $NAME"
fi

python /wptagent/wptagent.py --server $SERVER_URL --location $LOCATION $EXTRA_ARGS --xvfb --dockerized -vvvvv --shaper none
