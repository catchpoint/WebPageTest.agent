# Linux Headless Agent

To run the agent you add the Docker command and pass on the parameters as usual.

## Prerequisites to use traffic shaping in docker

**Experimental**: Running the agent with traffic shaping is experimental. It might
have influence on the host system network. Running multiple agents on the
same host might result in incorrect traffic shaping.

For traffic shaping to work correctly, you need to load the ifb module on the **host**:

    sudo modprobe ifb numifbs=1

Also, the container needs `NET_ADMIN` capabilities, so run the container with
`--cap-add=NET_ADMIN`.

To disable traffic-shaping, pass --shaper "none".

## Example

Build the image first (from project root), load ifb and start the container:

    sudo docker build --tag wptagent .
    sudo modprobe ifb numifbs=1
    sudo docker run -d \
      --cap-add=NET_ADMIN \
      wptagent --server "http://my-wpt-server.org/work/" \
      --location "docker-location" \
      --name "Docker Test"
