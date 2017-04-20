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

GIT_BRANCH="${GIT_BRANCH:-master}"
UPDATE_POLICY="${UPDATE_POLICY:-auto}"

function run_updates {
  git pull origin ${GIT_BRANCH}
  sudo apt-get update
  sudo apt-get -y upgrade
  sudo apt-get -y dist-upgrade
  sudo apt-get -y autoremove
  sudo npm update -g
}

function run_agent {
  python /wptagent/wptagent.py --server "${SERVER_URL}" --location "${LOCATION}" ${EXTRA_ARGS} --xvfb --dockerized -vvvvv
}

if [ "${UPDATE_POLICY}" == "auto" ]; then
  EXTRA_ARGS="${EXTRA_ARGS} --exit 60"
  while true; do
    run_updates
    run_agent
    echo "Exited, restarting"
    sleep 1
  done
else
  run_agent
fi