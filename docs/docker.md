# Docker Linux Headless Agent

The `Dockerfile` has multi-stage definition:
* **production**: Default stage, produce a image without debug features;
* **debug**: When running the produced image the wptagent script will wait for a debugger to.attach

## Build the Image

Arguments can be passed at build time:
* **TIMEZONE**: to set the timezone inside the container. Default `UTC.`

To build the production container with UTC timezone
```bash
docker build --tag wptagent .
```

changing the timezone at build time
```bash
docker build --build-arg TIMEZONE=EST .
```

To build the debug container
```bash
docker build --target debug --tag wptagent-debug .
```

## Prerequisites to use traffic shaping in docker
**Experimental**: Running the agent with traffic shaping is experimental. It might
have influence on the host system network. Running multiple agents on the
same host might result in incorrect traffic shaping.

For traffic shaping to work correctly, you need to load the ifb module on the **host**:
```bash
    sudo modprobe ifb numifbs=1
```

Also, the container needs `NET_ADMIN` capabilities, so run the container with 
`--cap-add=NET_ADMIN`.

To disable traffic-shaping, pass environment variable at docker un `SHAPER="none"`.

## Run the container
To run the agent, simply specify a few environment variables with docker:

- `SERVER_URL`: will be passed as `--server` (note: it must end with '/work/')
- `LOCATION`: will be passed as `--location`
- `KEY`: will be passed as `--key`
- `NAME`: will be passed as `--name` (optional)
- `SHAPER`: will be passed as `--shaper` (optional)
- `EXTRA_ARGS`: extra command-line options that will be passed through verbatim (optional)

Build the image first (from project root), load ifb and start it the container.

A typical run :
```bash
    sudo modprobe ifb numifbs=1
    docker build --tag wptagent .
    docker run -d \
      -e SERVER_URL="http://my-wpt-server.org/work/" \
      -e LOCATION="docker-location" \
      -e NAME="Docker Test" \
      --cap-add=NET_ADMIN \
      --init \
      wptagent
```

Additional parameters can be also passed as additional commands. 
A typical run in debug mode, note that we need to expose the port as `50000`:
```bash
sudo modprobe ifb numifbs=1
docker run -d \
    -e SERVER_URL=http://127.0.0.1:80/work/ \
    -e LOCATION=Test \
    --init \
    --cap-add=NET_ADMIN \
    -p 50000:50000 \
    wptagent-debug
    --key 123456789
```

## Container Disk Space Fills Up Quickly

If you see disk space within the container filling up rapidly and you notice
core dump files in the /wptagent folder, try adding `--shm-size=1g` to your Docker run
command. This can help resolve an issue with shared memory and headless Chrome in Docker.