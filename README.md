# wptagent
Cross-platform WebPageTest agent (currently supports Chrome only on Windows and Linux)

## Known Issues
* Not all features have been implemented yet (see list below)

## Installation
* Install docs are [here](docs/install.md)

## Command-line options
* **-v** : Increase verbosity (specify multiple times for more). -vvvv for full debug output.
* **--server** (required): URL for WebPageTest work (i.e. http://www.webpagetest.org/work/).
* **--location** (required): Location ID (as configured in locations.ini on the server).
* **--key** : Location key (if configured in locations.ini).
* **--name** : Agent name (defaults to the machine's hostname).
* **--exit** : Exit after the specified number of minutes.
    * Useful for running in a shell script that does some maintenence or updates periodically (like hourly).
* **--shaper** : Override default traffic shaper. Current supported values are:
    * none - Disable traffic-shaping (i.e. when root is not available).
* **--xvfb** : Use an xvfb virtual display for headless testing (Linux only).
* **--ec2** : Load config settings from EC2 user data.
* **--gce** : Load config settings from GCE user data.

Options for authenticating the agent with the server:
* **--username** : User name if using HTTP Basic auth with WebPageTest server.
* **--password** : Password if using HTTP Basic auth with WebPageTest server.
* **--cert** : Client certificate if using certificates to authenticate the WebPageTest server connection.
* **--certkey** : Client-side private key (if not embedded in the cert).


## Currently supported features
* Page Navigation
* Mobile Emulation
* Custom browser window size
* Multiple runs
* Test sharding
* First/Repeat View
* Ending tests at onload or by network activity (web10 test option)
* Network Waterfalls with request/response headers
* Disable Javascript
* Response Bodies
* CPU Utilization
* Bandwidth Utilization
* Traffic-shaping
* Screen Shots (JPEG with quality and PNG)
* Video Capture (60fps)
* Visual metrics (Start render, Speed Index, Visually Complete)
* User Timing Marks
* Navigation Timing
* Ignoring TLS Errors
* Minimum test duration
* Custom user agent strings
* Custom headers
* Custom command-line options
* Custom Metrics
* Dev Tools Timeline
* Javascript timing (execution/parse)
* Time to Interactive
* Trace Capture
* Multi-step tests
* Request blocking
* SPOF testing
* Optimization checks
* Exit after running for specified time (i.e. hourly)
* EC2/GCE config through user data
* Basic auth and client certificates for communicating with WebPageTest server
* Script Commands:
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
    * addHeader
    * setHeader (aliased to addHeader until devtools supports overriding headers)
    * setCookie
    * setABM

## Not yet supported (actively being worked on)
* Android Support (replace NodeJS agent)
* Improved request timing (from netlog)
* HTTP/2 Push reporting
* HTTP/2 Stream Details
* Lighthouse integration
* tcpdump
* Traceroute tests
* Browser installs/updates
* Windows general cleanup/health (temp files, downloads, killing processes, etc)
* Script Commands that will be translated into exec:
    * click (clickAndWait)
    * selectValue
    * sendClick
    * sendKeyDown
    * setInnerHTML
    * setInnerText
    * setValue
    * submitForm
* Other Script Commands:
    * requiredRequest
    * setDOMRequest
    * waitForJSDone (change semantics to console log message)
    * overrideHost (depends on support being added to dev tools)
    * if/else/endif

## Not Supported (no plans to implement)
* Netlog (rely on netlog trace events instead)
* Script Commands:
    * setDOMElement
    * waitForComplete
    * setDnsName
    * overrideHostUrl
    * ignoreErrors
    * logErrors
    * loadFile
    * loadVariables
    * minInterval
    * endInterval
    * expireCache
    * firefoxPref
    * resetHeaders
