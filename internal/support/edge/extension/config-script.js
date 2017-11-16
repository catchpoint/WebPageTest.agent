// Pull the config out of the DOM and relay it to the extension
var config = JSON.parse(document.getElementById("wptagentConfig").innerHTML);
browser.runtime.sendMessage(config);