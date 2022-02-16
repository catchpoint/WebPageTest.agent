# JSON test options
wptagent runs test jobs specified as a JSON object with the keys specified below. The only required keys are either "url" or "script" telling the Agent what site to test, everything else has default values that can be overridden.

## Common Options
* **url** (string) : URL to test (required unless "script" is specified).
* **script** (string) : Test script to use for automating the browser (takes precedence over "url" if specified).
* **browser** (string) : Browser to use for testing. Defaults to "Chrome"
* **runs** (int) : Number of runs to test (defaults to 1).
* **fvonly** (int) : Set to 0 to enable repeat-view testing. Defaults to 1 (first view only).

## Video Settings
* **Capture Video** (int) : Set to 0 to disable uploading of video frames (defaults to 1).
* **disable_video** (int) : Set to 1 to disable video capture entirely (will also disable any metrics that depend on video).
* **fps** (int) : Desktop video capture frames per second (defaults to 10).
* **fullSizeVideo** (int) : Set to 1 to upload full-resolution video frames instead of thumbnails.
* **keepvideo** (int) : Set to 1 to upload the raw, unprocessed mp4 video with the test results (for debugging).
* **thumbsize** (int) : Size of the video capture thumbnails in pixels (defaults to 400).

## Connection and Device Emulation
* **bwIn** (int) : Download bandwidth in kilo bits per second (i.e. 5000 = 5Mbps).
* **bwOut** (int) : Upload bandwidth in kilo bits per second.
* **latency** (int) : Additional connection latency to apply in milliseconds (full round-trip latency).
* **plr** (float) : Packet loss rate.
* **shaperLimit** (int) : Netem packet limit for the shaper (defaults to unlimited).
* **mobile** (int) : Set to 1 to enable mobile emulation (Chrome-only).
* **width** (int) : Viewport Width.
* **height** (int) : Viewport Height.
* **dpr** (float) : Viewport Device Pixel Ratio.
* **lat** (string) : Latitude to use for Geo Location.
* **lng** (string) : Longitude to use for Geo Location.
* **throttle_cpu** (float) : Multiplier to slow down the CPU (i.e. 2.5 will apply a 2.5 times slowdown to the CPU performance - Chrome-only).
* **bypass_cpu_normalization** (int) : Set to 1 to disable the logic that adjusts the CPU throttling based on the performance of the test machine.

## Browser Settings
* **AppendUA** (string) : String to append to the browser user agent string.
* **browser_height** (int) : Browser window height.
* **browser_width** (int) : Browser window width.
* **customBrowserUrl** (string) : URL to download APK for custom android browser.
* **customBrowserMD5** (string) : md5 hash of custom browser APK.
* **ignoreSSL** (int) : Set to 1 to Ignore SSL errors.
* **keepua** (int) : Set to 1 to use the default browser user agent string with no modifications.
* **UAModifier** (string) : Custom modifier string to use in the browser user agent string (defaults to "PTST")
* **uastring** (string) : Custom user agent string.

## Chrome-specific settings
* **addCmdLine** (string) : Additional command-line params to use.
* **coverage** (int) : Set to 1 to enable JavaScript and CSS coverage reporting (increased test overhead).
* **disableAVIF** (int) : Set to 1 to disable support for the AVIF image format.
* **disableJXL** (int) : Set to 1 to disable support for the JPEG XL image format.
* **disableWEBP** (int) : Set to 1 to disable support for the WEBP image format.
* **discard_timeline** (int) : Set to 1 to discard the timeline file after processing (defaults to uploading with the test results).
* **dtShaper** (int) : Set to 1 to use Chrome's traffic-shaping instead of the default shaping used by the agent.
* **lighthouse** (int) : Set to 1 to enable running a lighthouse test.
* **lighthouseConfig** (string) : JSON Lighthouse test config.
* **lighthouseScreenshots** (int) : Set to 1 to keep the embedded screenshot images in the lighthouse report.
* **lighthouseThrottle** (int) : Set to 1 to use lighthouse throttling instead of the agent throttling.
* **lighthouseTrace** (int) : Set to 1 to capture a trace from the lighthouse test.
* **netlog** (int) : Set to 1 to enable Netlog capture.
* **profiler** (int) : Set to 1 to enable the V8 sampling profiler (MUCH larger trace files).
* **timeline** (int) : Set to 0 to disable timeline capture (main thread execution timings).
* **timeline_fps** (int) : Set to 1 to enable capture of the timeline frame timings.
* **trace** (int) : Set to 1 to enable reporting of the trace file from Chrome.
* **traceCategories** (string) : Comma-delimited list of trace categories to capture.
* **v8rcs** (int) : Set to 1 to enable recording of the V8 runtime call stats.

## Other test options
* **axe** (int) : Set to 0 to stop Axe accessibility testing from running.
* **axe_categories** (string) : Comma-delimited list of [Axe-Core tags](https://www.deque.com/axe/core-documentation/api-documentation/#axe-core-tags) to determine which audits to run. Defaults to: 'wcag2a,wcag2aa'
* **block** (string) : Space-separated list of URL patterns to block.
* **blockDomains** (string) : Space-separated list of domains to block (fully-qualified domain names, i.e. www.example.com).
* **bodies** (int) : Set to 1 to store response bodies for text resources (html, JS, css).
* **crux_api_key** (string) : Google Chrome User Experience Report API key to use to fetch CrUX stats for the test URL.
* **customMetrics** (dictionary of strings) : Keys are the name of the custom metric and values are the JavaScript code to execute to collect the custom metric.
* **debug** (int) : Set to 1 to enable uploading of the debug log with the test result.
* **htmlbody** (int) : Set to 1 to store the response body for the main HTML resource (subset of "bodies").
* **imageQuality** (int) : JPEG quality setting for compressing thumbnails and video frames. default = 30.
* **injectScript** (string) : Javascript to inject into the loading page at the start of loading.
* **max_requests** (int) : Maximum number of requests to allow before terminating the test (disabled by default)
* **noheaders** (int) : Set to 1 to strip all request and response headers from the test results (for sensitive data tests)
* **noopt** (int) : Set to 1 to disable the optimization checks.
* **noscript** (int) : Set to 1 to disable JavaScript.
* **pngScreenShot** (int) : Set to 1 to return full-resolution PNG screen shots instead of JPEG.
* **renderVideo** (int) : Set to 1 to enable rendering of the resulting page load video from the video frames.
* **securityInsights** (int) : Set to 1 to enable running the Snyk security insights custom script.
* **tcpdump** (int) : Set to 1 to enable tcpdump capture.
* **time** (int) : Minimum test time in seconds.
* **timeout** (int) : Maximum time in seconds to allow for each run of the test (defaults to 120).
* **type** (string) : Set to "traceroute" to run a traceroute test or "lighthouse" to run a lighthouse-only test.
* **wappalyzer** (int) : Set to 0 to stop Wappalyzer detection from running.
* **warmup** (int) : Number of "warmup" runs to run before recording the test (defaults to 0).
* **web10** (int) : Set to 1 to stop the test at "onload" instead of waiting for network activity.

## Non-CLI Options
For completeness but not used when testing manually through the CLI.
* **Test ID** (string) : WebPageTest Test ID for the test. Only used when the test originated from the WebPageTest web UI.
* **jobID** (string) : Scheduled job ID when using Scheduler for test queuing.
* **work_server** (string) : Server to post the test result back to.
