# Linux Headless Agent

To run the agent, simply specify a few environment variables with docker:

* `SERVER_URL`: will be passed as `--server` (note: it must end with '/work/')
* `LOCATION`: will be passed as `--location`
* `KEY`: will be passed as `--key`
* `NAME`: will be passed as `--name` (optional)
* `SHAPER`: will be passed as `--shaper` (optional)
* `EXTRA_ARGS`: extra command-line options that will be passed through verbatim (optional)

## Prerequisites to use traffic shaping in docker
**Experimental**: Running the agent with traffic shaping is experimental. It might
have influence on the host system network. Running multiple agents on the
same host might result in incorrect traffic shaping.

For traffic shaping to work correctly, you need to load the ifb module on the **host**:

    sudo modprobe ifb numifbs=1

Also, the container needs `NET_ADMIN` capabilities, so run the container with 
`--cap-add=NET_ADMIN`.

To disable traffic-shaping, pass SHAPER="none".

## Container Disk Space Fills Up Quickly

If you see disk space within the container filling up rapidly and you notice
core dump files in the /wptagent folder, try adding `--shm-size=1g` to your Docker run
command. This can help resolve an issue with shared memory and headless Chrome in Docker.

## Example

Build the image first (from project root), load ifb and start it the container:

    sudo docker build --tag wptagent .
    sudo modprobe ifb numifbs=1
    sudo docker run -d \
      -e SERVER_URL="http://my-wpt-server.org/work/" \
      -e LOCATION="docker-location" \
      -e NAME="Docker Test" \
      --cap-add=NET_ADMIN \
      --init \
      wptagent

