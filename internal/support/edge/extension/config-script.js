// Pull the config out of the DOM and relay it to the extension
var config = JSON.parse(document.getElementById("wptagentConfig").innerText);
browser.runtime.sendMessage(config);