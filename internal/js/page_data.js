(function() {
var pageData = {};
pageData["document_URL"] = document.location.href;
pageData["document_hostname"] = document.location.hostname;
pageData["document_origin"] = document.location.origin;
pageData['viewport'] = {
  'width': window.innerWidth,
  'height': window.innerHeight,
  'dpr': window.devicePixelRatio
};
var domCount = document.documentElement.getElementsByTagName("*").length;
if (domCount === undefined)
  domCount = 0;
pageData["domElements"] = domCount;
function addTime(name) {
  try {
    if (window.performance.timing[name] > 0) {
      pageData[name] = Math.max(0, Math.round(
          window.performance.timing[name] -
          window.performance.timing["navigationStart"]));
    }
  } catch(e) {}
};
addTime("domInteractive");
addTime("domContentLoadedEventStart");
addTime("domContentLoadedEventEnd");
addTime("timeToContentfulPaint");
addTime("domComplete");
addTime("loadEventStart");
addTime("loadEventEnd");
addTime("timeToFirstInteractive");
try {
    if (window.performance.timing['timeToDOMContentFlushed']) {
        pageData["domContentFlushed"] = window.performance.timing.timeToDOMContentFlushed - window.performance.timing.fetchStart;
    }
} catch(e) {
}
pageData["firstPaint"] = 0;
// Try the standardized paint timing api
try {
  var entries = performance.getEntriesByType('paint');
  var navStart = performance.getEntriesByType("navigation").length > 0 ?  performance.getEntriesByType("navigation")[0].startTime : 0;
  for (var i = 0; i < entries.length; i++) {
    var entryTime = entries[i].startTime - navStart;
    pageData["PerformancePaintTiming." + entries[i]['name']] = entryTime;
    if (entries[i]['name'] == 'first-paint') {
      pageData["firstPaint"] = entryTime;
    }
  }
} catch(e) {
}
if (!pageData["firstPaint"]) {
  try {
    if (window.performance.timing["timeToNonBlankPaint"] > 0) {
      pageData["firstPaint"] = Math.max(0, Math.round(
        window.performance.timing["timeToNonBlankPaint"] -
        window.performance.timing["navigationStart"]));
    }
  } catch(e) {}
}
if (!pageData["firstPaint"] &&
    window["chrome"] !== undefined &&
    window.chrome["loadTimes"] !== undefined) {
 var chromeTimes = window.chrome.loadTimes();
 if (chromeTimes["firstPaintTime"] !== undefined &&
     chromeTimes["firstPaintTime"] > 0) {
   var startTime = chromeTimes["requestTime"] ?
       chromeTimes["requestTime"] : chromeTimes["startLoadTime"];
   if (chromeTimes["firstPaintTime"] >= startTime)
     pageData["firstPaint"] = Math.round(
         (chromeTimes["firstPaintTime"] - startTime) * 1000.0);
 }
}
return pageData;
})();
