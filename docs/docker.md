# Linux Headless Agent

To run the agent, simply specify a few environment variables with docker:

* `SERVER_URL`: will be passed as `--server`
* `LOCATION`: will be passed as `--location`
* `NAME`: will be passed as `--name` (optional)
* `UPDATE_POLICY`: If set to `always` (default) the container automatically installs updates.
  Otherwise it simply runs the agent.
* `GIT_BRANCH`: The branch to update the agent code if updates are enabled. Defaults to `master`

## Prerequisites to use traffic shaping in docker
**Experimental**: Running the agent with traffic shaping is experimental. It might
have influence on the host system network. Running multiple agents on the
same host might result in incorrect traffic shaping.

For traffic shaping to work correctly, you need to load the ifb module on the **host**:

    sudo modprobe ifb numifbs=1

Also, the container needs `NET_ADMIN` capabilities, so run the container with 
`--cap-add=NET_ADMIN`.

## Example

Build the image first (from project root), load ifb and start it the container:

    sudo docker build --tag wptagent .
    sudo modprobe ifb numifbs=1
    sudo docker run -d \
      -e SERVER_URL="http://my-wpt-server.org/work/" \
      -e LOCATION="docker-location" \
      -e NAME="Docker Test" \
      --cap-add=NET_ADMIN
      wptagent

