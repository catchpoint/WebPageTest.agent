# wptagent

[![travis](https://img.shields.io/travis/WPO-Foundation/wptagent.svg?label=travis)](http://travis-ci.org/WPO-Foundation/wptagent)

Cross-platform WebPageTest agent

## Supported Platforms/Browsers
Chromium-based browsers are the only browsers that currently supports manipulating requests (changing headers, blocking requests, etc).  Firefox and Safari do not currently support capturing response bodies and running optimization checks.  All browsers should support basic page loading, scripts and video capture on all platforms.  Traffic-shaping is supported on all platforms as well.

### Linux (with display or headless with Xvfb)
* Chrome (Stable, Beta and Unstable)
* Firefox (Stable and Nightly)
* Opera (Stable, Beta and Developer)
* Brave (Stable, Beta, Dev and Nightly)
* Microsoft Edge (Dev)
* Epiphany (Ubuntu 20.04+)
* Vivaldi

### Windows
* Chrome (Stable, Beta, Dev and Canary)
* Firefox (Stable, ESR, Developer Edition, Beta and Nightly)
* Microsoft Edge (Legacy and Chromium-based)
* Internet Explorer
* Opera (Stable, Beta and Developer)
* Brave (Stable, Beta, Dev and Nightly)

### OSX - Intel and Apple Silicon
* Chrome (Stable and Canary)
* Firefox (Stable and Nightly)
* Safari (iOS Simulator)

### Android (requires a tethered host - Raspberry Pi's preferred)
* Chrome (Stable, Beta, Dev and Canary)
* Samsung Internet
* Several browsers run as "black box" tests (single page load, visual metrics only):
    * Chrome (Stable, Beta, Dev and Canary)
    * Samsung Browser
    * Opera
    * Opera Mini
    * UC Browser
    * UC Mini
    * Firefox (Stable and Beta)

## Known Issues
* Internet Explorer does not support manipulating requests (adding headers, blocking requests, etc)

## Installation
* Install docs are [here](docs/install.md)

## Run with docker
Check out [the docker instructions](docs/docker.md) for information on how to
run the agent in a docker container.

## Command-line options
### Basic agent config
* **-v** : Increase verbosity (specify multiple times for more). -vvvv for full debug output.
* **--name** : Agent name (defaults to the machine's hostname).
* **--exit** : Exit after the specified number of minutes.
    * Useful for running in a shell script that does some maintenance or updates periodically (like hourly).
* **--dockerized**: The agent is running inside a docker container.
* **--ec2** : Load config settings from EC2 user data.
* **--gce** : Load config settings from GCE user data.
* **--log** : Log critical errors to the given file.
* **--healthcheckport** : HTTP Health check port. Defaults to 8889, set to 0 to disable. Returns 200 if the agent is running and communicating with the server, 503 otherwise.

### Video capture/display settings
* **--xvfb** : Use an xvfb virtual display for headless testing (Linux only).
* **--fps** : Video capture frame rate (defaults to 10). Valid range is 1-60. (Linux only).

### Server/location configuration
* **--server** (required): URL for WebPageTest work (i.e. http://www.webpagetest.org/work/).
* **--validcertificate**: Validate server certificates (HTTPS server, defaults to False).
* **--location** (required): Location ID (as configured in locations.ini on the server).
* **--key** : Location key (if configured in locations.ini).

### Traffic-shaping options (defaults to host-based)
* **--shaper** : Override default traffic shaper. Current supported values are:
    * none - Disable traffic-shaping (i.e. when root is not available).
    * netem,\<interface\> - Use NetEm for bridging rndis traffic (specify outbound interface).  i.e. --shaper netem,eth0
    * remote,\<server\>,\<down pipe\>,\<up pipe\> - Connect to the remote server over ssh and use pre-configured dummynet pipes (ssh keys for root user should be pre-authorized).
    * chrome - Use Chrome's dev tools traffic-shaping. Only supports Chromium browsers and should be used as a last resort.

### Android testing options
* **--android** : Run tests on an attached android device.
* **--device** : Device ID (only needed if more than one android device attached).
* **--gnirehtet** : Use gnirehtet for reverse-tethering. You will need to manually approve the vpn once per mobile device. Valid options are:
    * <external interface>,<dns>: i.e. --gnirehtet eth0,8.8.8.8
* **--vpntether** : (Android < 7) Use vpn-reverse-tether for reverse-tethering. You will need to manually approve the vpn once per mobile device. Valid options are:
    * <external interface>,<dns>: i.e. --vpntether eth0,8.8.8.8
* **--vpntether2** : Use vpn-reverse-tether v 2 for reverse-tethering. This is the recommended way to reverse-tether devices. You will need to manually approve the vpn once per mobile device. Valid options are:
    * <external interface>,<dns>: i.e. --vpntether2 eth0,8.8.8.8
* **--simplert** : Use [SimpleRT](https://github.com/vvviperrr/SimpleRT) for reverse-tethering.  The APK should be installed manually (adb install simple-rt/simple-rt-1.1.apk) and tested once manually (./simple-rt -i eth0 then disconnect and re-connect phone) to dismiss any system dialogs.  The ethernet interface and DNS server should be passed as options:
    * <interface>,<dns1>: i.e. --simplert eth0,8.8.8.8
* **--rndis** : (deprecated) Enable reverse-tethering over rndis (Android < 6.0).  Valid options are:
    * <ip>/<network>,<gateway>,<dns1>,<dns2>: Static Address.  i.e. --rndis 192.168.0.8/24,192.168.0.1,8.8.8.8,8.8.4.4
    * dhcp

### Options for authenticating the agent with the server:
* **--username** : User name if using HTTP Basic auth with WebPageTest server.
* **--password** : Password if using HTTP Basic auth with WebPageTest server.
* **--cert** : Client certificate if using certificates to authenticate the WebPageTest server connection.
* **--certkey** : Client-side private key (if not embedded in the cert).

### Options for running tests locally on the command-line:
The result of the test will be output to stdout as JSON. If a server, location and key are provided then the test will be uploaded to the given WebPageTest server and the test ID will be returned in the output JSON.
* **--testurl** : Run a one-off test of the given URL using the command-line (required unless a testspec is provided)
    * <url> : URL to test
* **--testspec** : Provide a full [JSON file](docs/test_options.md) with test parameters
    * <path> : Path to the JSON test options
* **--browser** : Specify the browser to test in (can also be specified in the JSON file)
    * <url> : Browser to test in
* **--testout** : Output format fot the test result. Valid options are:
    * id : Test ID (if tests are uploaded to a server/location)
    * url : URL to test result (if tests are uploaded to a server/location)
    * json : JSON-formatted raw test result
* **--testoutdir** : Output directory for the raw test results (optional)
    * <path> : Path to the output directory
* **--testruns** : Number of runs to test
    * <runs> : Defaults to 1
* **--testrv** : Include repeat view (defaults to only testing first view)

## Currently supported features
* Feature complete except as noted below (for Windows, Linux, Mac and Android devices)
* Supported Script Commands:
    * navigate
    * exec (execAndWait)
    * block
    * sleep
    * logData
    * combineSteps
    * setEventName
    * setUserAgent
    * setBrowserSize
    * setViewportSize
    * setDeviceScaleFactor
    * setActivityTimeout
    * setTimeout
    * blockDomains
    * blockDomainsExcept
    * setDns
    * setDnsName
    * setHeader
    * addHeader (can not add multiple values for the same header, effectively the same as setHeader)
    * resetHeaders
    * setCookie
    * setABM
    * click (clickAndWait)
    * selectValue
    * sendClick
    * setInnerHTML
    * setInnerText
    * setValue
    * submitForm
    * overrideHost

## Not Supported (no plans to implement)
* Script Commands:
    * sendKeyDown
    * setDOMElement
    * waitForComplete
    * overrideHostUrl
    * ignoreErrors
    * logErrors
    * loadFile
    * loadVariables
    * minInterval
    * endInterval
    * expireCache
    * requiredRequest
    * setDOMRequest
    * waitForJSDone (change semantics to console log message)
    * if/else/endif

## Contributing
There are 2 separate lines of development under different licenses and pull requests are accepted to either of them.  The master branch where most active development is occurring is under the [Polyform Shield 1.0.0 license](LICENSE.md) and there is an "apache" branch which is under the more permissive Apache 2.0 license.
