(function() {
var pageData = {};
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
addTime("loadEventStart");
addTime("loadEventEnd");
pageData["firstPaint"] = 0;
try {
  if (window.performance.timing["timeToNonBlankPaint"] > 0) {
    pageData["firstPaint"] = Math.max(0, Math.round(
      window.performance.timing["timeToNonBlankPaint"] -
      window.performance.timing["navigationStart"]));
  }
} catch(e) {}
if (window["chrome"] !== undefined &&
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
