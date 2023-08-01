# `wptagent`

Cross-platform WebPageTest agent

- 🥡 [Installation instructons](docs/install.md)
- 🐋 [Docker instructions](docs/docker.md)

## Contributing

There are separate lines of development under different licenses (pull requests accepted to either):

- The `master` branch where most active development occurs has the [Polyform Shield 1.0.0 license](LICENSE.md)
- The `apache` branch has the more permissive [Apache 2.0 license](https://opensource.org/licenses/Apache-2.0)

## Supported Platforms/Browsers

- Chromium-based browsers are the only ones that currently support manipulating requests (changing headers, blocking requests, etc). Firefox and Safari do not currently support capturing response bodies and running optimization checks.
- All browsers should support basic page loading, scripts, and video capture on all platforms.
- Traffic-shaping is supported on all platforms.

### ⚠️ Known Issues

- Internet Explorer does not support manipulating requests (adding headers, blocking requests, etc)

### Linux (with display, or headless with [Xvfb])

- Chrome: Stable, Beta, and Unstable
- Firefox: Stable and Nightly
- Opera: Stable, Beta, and Developer
- Brave: Stable, Beta, Dev, and Nightly
- Microsoft Edge: Dev
- Epiphany: Ubuntu 20.04+
- Vivaldi

### Windows

- Chrome: Stable, Beta, Dev and Canary
- Firefox: Stable, ESR, Developer Edition, Beta, and Nightly
- Microsoft Edge: Legacy and Chromium-based
- Internet Explorer
- Opera: Stable, Beta and Developer
- Brave: Stable, Beta, Dev and Nightly

### MacOS (Intel and Apple Silicon)

- Chrome: Stable and Canary
- Firefox: Stable and Nightly
- Safari: iOS Simulator

### Android (requires a tethered host; Raspberry Pi preferred)
- Chrome (Stable, Beta, Dev, and Canary)
- Samsung Internet
- Several browsers run as “black box” tests (single page load, only visual metrics):
  - Chrome (Stable, Beta, Dev and Canary)
  - Samsung Browser
  - Opera
  - Opera Mini
  - UC Browser
  - UC Mini
  - Firefox (Stable and Beta)

## Command-line options

### Basic agent config

* `-v`, `-vv`, `-vvv`…: Increase verbosity (specify multiple times for more). `-vvvv` for full debug output.
* `--name`: Agent name (defaults to the machine’s `hostname`).
* `--exit`: Exit after the specified number of minutes. Useful for running in a shell script that does maintenance or periodic updates (like hourly).
* `--dockerized`: The agent is running inside a docker container.
* `--ec2`: Load config settings from EC2 user data.
* `--gce`: Load config settings from GCE user data.
* `--log`: Log critical errors to the given file.
* `--noidle`: Doesn't wait for system idle at any point.

* `--healthcheckport`: HTTP Health check port (defaults to 8889). Set to 0 to disable. Returns 200 if the agent is running and communicating with the server, 503 otherwise.
* `--har` : Generate a per-run HAR file as part of the test result (defaults to False).

### Video capture/display settings (Linux only)

- `--xvfb`: Use an [Xvfb] virtual display for headless testing.
- `--fps`: Video capture frame rate (defaults to 10). Valid range is 1–60.

[Xvfb]: https://en.wikipedia.org/wiki/Xvfb

### Server/location configuration

* `--server` (required): URL for WebPageTest work. Example: `https://www.webpagetest.org/work/`.
* `--location` (required): Location ID (as configured in the server’s `locations.ini`).
* `--validcertificate`: Validate server certificates (HTTPS server, defaults to `False`).
* `--key` : Location key (if configured in `locations.ini`).

### Traffic-shaping (defaults to host-based)

- `--shaper`: Override default traffic shaper. Supported values:
  - `none`: Disable traffic-shaping (i.e. when you can’t run as root).
  - `netem,<interface>`: Use [NetEm] to bridge reverse-tethered traffic (specify outbound interface). Example: `--shaper netem,eth0`
  - `remote,<server>,<down pipe>,<up pipe>`: Connect to the remote server over `ssh` and use preconfigured [dummynet] pipes. SSH keys for root user should be pre-authorized.
  - `chrome`: Use Chrome DevTools’s traffic-shaping. Only for Chromium-based browsers, and [as a last resort because of inaccuracy][devtools-inaccuracy].

[NetEm]: https://wiki.linuxfoundation.org/networking/netem
[dummynet]: http://info.iet.unipi.it/~luigi/dummynet/
[devtools-inaccuracy]: https://blog.webpagetest.org/posts/full-throttle-comparing-packet-level-and-dev-tools-throttling/

### Android testing

- `--android`: Run tests on an attached Android device.
- `--device`: Device ID, if more than one Android is attached.
- `--gnirehtet`: Use the `gnirehtet` tool for reverse-tethering. You will need to manually approve the vpn once per mobile device. Valid options are:
    * `<external interface>,<dns>`: e.g. `--gnirehtet eth0,8.8.8.8`
- `--vpntether`: (Android 7+) Use vpn-reverse-tether for reverse-tethering. You will need to manually approve the vpn once per mobile device. Valid options are:
    * `<external interface>,<dns>`: e.g. `--vpntether eth0,8.8.8.8`
- `--vpntether2` (recommended): Use vpn-reverse-tether v2 for reverse-tethering. You will need to manually approve the VPN once per mobile device. Valid options:
    * `<external interface>,<dns>`: e.g. `--vpntether2 eth0,8.8.8.8`
- `--simplert`: Use [SimpleRT] for reverse-tethering. The APK should be installed manually (`adb install simple-rt/simple-rt-1.1.apk`) and tested once manually (`./simple-rt -i eth0`, then disconnect and re-connect phone) to dismiss any system dialogs. The ethernet interface and DNS server should be passed as options:
    * `<interface>,<dns1>`: i.e. `--simplert eth0,8.8.8.8`
- `--rndis` (deprecated): Enable reverse-tethering over rndis (Android 6+). Valid options:
    * `--rndis <ip>/<network>,<gateway>,<dns1>,<dns2>`: Static Address. e.g. `--rndis 192.168.0.8/24,192.168.0.1,8.8.8.8,8.8.4.4`
    * `--rndis dhcp`
   
[SimpleRT]: https://github.com/vvviperrr/SimpleRT

### Authenticating the agent with the server
   
- `--username`: Username if using [HTTP Basic Auth](https://developer.mozilla.org/en-US/docs/Web/HTTP/Authentication#basic_authentication_scheme) with WebPageTest server.
- `--password`: Password if using HTTP Basic Auth with WebPageTest server.
- `--cert`: Client certificate if using certificates to authenticate the WebPageTest server connection.
- `--certkey`: Client-side private key (if not embedded in the client certificate).

### Running tests locally on the command-line
   
The test result is written as JSON to `stdout`. If a server, location, and key are provided, then the test will be uploaded to the given WebPageTest server and the test ID returned in the output JSON.
   
- `--testurl`: Test the given URL via the command line (required unless `--testspec` is provided)
- `--testspec`: Path to a full [JSON file with test parameters](docs/test_options.md)
- `--browser`: What browser to test in (can also be specified in the JSON file)
- `--testout`: Output format fot the test result. Valid options:
  - `id`: Test ID (if tests are uploaded to a server/location)
  - `url`: URL to test result (if tests are uploaded to a server/location)
  - `json`: JSON-formatted raw test result
- `--testoutdir` (optional): Output directory for the raw JSON test results
- `--testruns`: Number of runs to test. Defaults to 1.
- `--testrv`: Include repeat view (defaults to only testing first view)

## Supported features

The following [Script Commands](https://docs.webpagetest.org/scripting/) are supported on Windows, Linux, Mac, and Android:
   
- `navigate`
- `exec` and `execAndWait`
- `block`
- `sleep`
- `logData`
- `combineSteps`
- `setEventName`
- `setUserAgent`
- `setBrowserSize`, `setViewportSize`, and `setDeviceScaleFactor`
- `setActivityTimeout` and `setTimeout`
- `blockDomains` and `blockDomainsExcept`
- `setDns` and `setDnsName
- `setHeader` and `addHeader` (`addHeader` add multiple values for the same header, effectively the same as `setHeader`)
- `resetHeaders`
- `setCookie`
- `setABM`
- `click`, `clickAndWait`, and `sendClick`
- `selectValue` and `setValue`
- `setInnerHTML` and `setInnerText`
- `submitForm`
- `overrideHost`

### Unsupported

There are no plans to implement the following [Script Commands](https://docs.webpagetest.org/scripting/):

- `sendKeyDown`
- `setDOMElement`
- `waitForComplete`
- `overrideHostUrl`
- `ignoreErrors`
- `logErrors` (TODO: [can’t find any mention of this in this GitHub organization](https://github.com/search?q=org%3AWPO-Foundation+logErrors&type=code)?)
- `loadFile`
- `loadVariables`
- `minInterval`
- `endInterval`
- `expireCache`
- `requiredRequest`
- `setDOMRequest`
- `waitForJSDone` (change semantics to `console.log` message)
- `if`, `else`, and `endif`
