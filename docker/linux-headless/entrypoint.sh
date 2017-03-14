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

if [ -z "$NAME" ]; then
  echo >&2 'NAME not set'
  exit 1
fi

python /wptagent/wptagent.py --server $SERVER_URL --location $LOCATION --name $NAME --xvfb -vvvvv --shaper none
