# wptagent
Cross-platform WebPageTest agent (currently supports Chrome only on Windows and Linux)

## Currently supported features
* Page Navigation
* Mobile Emulation
* Custom browser window size
* Multiple runs
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
* Custom Metrics
* Dev Tools Timeline
* Javascript timing (execution/parse)
* Time to Interactive
* Trace Capture
* Multi-step tests
* Request blocking
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
    * setTimeout

## Not yet supported (actively being worked on)
* Android Support (replace NodeJS agent)
* Optimization checks
* Improved request timing (from netlog)
* HTTP/2 Push reporting
* HTTP/2 Stream Details
* Lighthouse integration
* Test sharding
* Ignoring SSL Errors
* Disable Javascript
* tcpdump
* Custom user agent strings
* Minimum test duration
* Custom headers
* Custom command-line options
* SPOF
* Traceroute tests
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
    * setActivityTimeout
    * requiredRequest
    * setDOMRequest
    * setTimeout
    * waitForJSDone (change semantics to console log message)
    * blockDomains
    * setCookie
    * setDns
    * overrideHost (depends on support being added to dev tools)
    * addHeader
    * setHeader (depends on support being added to dev tools)
    * resetHeaders
    * if/else/endif
    * setViewportSize

## No Supported (no plans to implement)
* Netlog (rely on netlog trace events instead)
* Script Commands:
    * setABM
    * setDOMElement
    * waitForComplete
    * blockDomainsExcept
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
