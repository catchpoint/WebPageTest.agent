var SERVER = "http://127.0.0.1:8888/"

var send_message = function(event, body = '') {
  if (body.length) {
    fetch(SERVER + event, {method: 'POST', body: body});
  } else {
    fetch(SERVER + event);
  }
};

// Navigation handlers
browser.webNavigation.onBeforeNavigate.addListener(evt => {
  send_message('webNavigation.onBeforeNavigate', JSON.stringify(evt));
}, {
  url: [{schemes: ["http", "https"]}]}
);

browser.webNavigation.onCommitted.addListener(evt => {
  send_message('webNavigation.onCommitted', JSON.stringify(evt));
}, {
  url: [{schemes: ["http", "https"]}]}
);

browser.webNavigation.onDOMContentLoaded.addListener(evt => {
  send_message('webNavigation.onDOMContentLoaded', JSON.stringify(evt));
}, {
  url: [{schemes: ["http", "https"]}]}
);

browser.webNavigation.onCompleted.addListener(evt => {
  send_message('webNavigation.onCompleted', JSON.stringify(evt));
}, {
  url: [{schemes: ["http", "https"]}]}
);

browser.webNavigation.onErrorOccurred.addListener(evt => {
  send_message('webNavigation.onErrorOccurred', JSON.stringify(evt));
}, {
  url: [{schemes: ["http", "https"]}]}
);

// Request handlers
browser.webRequest.onBeforeRequest.addListener(details => {
  if (details.url.substring(0, 22) != 'http://127.0.0.1:8888/')
    send_message('webRequest.onBeforeRequest', JSON.stringify(details));
}, {urls: ["<all_urls>"]});

browser.webRequest.onSendHeaders.addListener(details => {
  if (details.url.substring(0, 22) != 'http://127.0.0.1:8888/')
    send_message('webRequest.onSendHeaders', JSON.stringify(details));
}, {urls: ["<all_urls>"]}, ["requestHeaders"]);

browser.webRequest.onHeadersReceived.addListener(details => {
  if (details.url.substring(0, 22) != 'http://127.0.0.1:8888/')
    send_message('webRequest.onHeadersReceived', JSON.stringify(details));
}, {urls: ["<all_urls>"]}, ["responseHeaders"]);

browser.webRequest.onResponseStarted.addListener(details => {
  if (details.url.substring(0, 22) != 'http://127.0.0.1:8888/')
    send_message('webRequest.onResponseStarted', JSON.stringify(details));
}, {urls: ["<all_urls>"]}, ["responseHeaders"]);

browser.webRequest.onBeforeRedirect.addListener(details => {
  if (details.url.substring(0, 22) != 'http://127.0.0.1:8888/')
    send_message('webRequest.onBeforeRedirect', JSON.stringify(details));
}, {urls: ["<all_urls>"]}, ["responseHeaders"]);

browser.webRequest.onCompleted.addListener(details => {
  if (details.url.substring(0, 22) != 'http://127.0.0.1:8888/')
    send_message('webRequest.onCompleted', JSON.stringify(details));
}, {urls: ["<all_urls>"]}, ["responseHeaders"]);

browser.webRequest.onErrorOccurred.addListener(details => {
  if (details.url.substring(0, 22) != 'http://127.0.0.1:8888/')
    send_message('webRequest.onErrorOccurred', JSON.stringify(details));
}, {urls: ["<all_urls>"]});

// Let wptagent know we started
send_message('wptagent.started');
