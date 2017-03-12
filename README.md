# wptagent
Cross-platform WebPageTest agent (currently supports Chrome only on Windows and Linux)

## Currently supported features
* Page Navigation
* Mobile Emulation
* Custom browser window size
* Multiple runs
* Test sharding
* First/Repeat View
* Ending tests at onload or by network activity (web10 test option)
* Network Waterfalls with request/response headers
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
* EC2/GCE config through user data
* Browser installs/updates
* Exit after running for specified time (i.e. hourly)
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
* Disable Javascript
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
