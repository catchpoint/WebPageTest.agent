(function() {
// Just handle FF for now, add more browsers later
if (navigator.userAgent.indexOf('Firefox') > -1) {
  var offset = navigator.userAgent.indexOf('Firefox');
  return {
    browser_name: 'Firefox',
    browser_version: navigator.userAgent.substring(offset + 8)
  }
} else {
  return {};
  }
 })();
