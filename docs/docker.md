# Linux Headless Agent

To run the agent, simply specify a few environment variables with docker:

* `SERVER_URL` will be passed as `--server`
* `LOCATION` will be passed as `--location`
* `NAME` will be passed as `--name` (optional)

## Example

Build the image first (from project root), and start it

    sudo docker build --tag wptagent .
    sudo docker run -d \
      -e SERVER_URL="http://my-wpt-server.org/work/" \
      -e LOCATION="docker-location" \
      -e NAME="Docker Test" \
      wptagent


## Known issues
* Traffic shaping does not yet work
