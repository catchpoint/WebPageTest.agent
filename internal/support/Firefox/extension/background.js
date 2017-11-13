var SERVER = "http://127.0.0.1:8888/";
var messages = '';
var message_timer = undefined;
var last_send = undefined;

var send_messages = function() {
  message_timer = undefined;
  last_send = performance.now();
  message_headers = new Headers({
    "Content-Type": "application/json",
    "Content-Length": messages.length.toString()
  });
  fetch(SERVER + 'messages',
        {method: 'POST', headers: message_headers, body: messages});
  messages = '';
};

var send_message = function(event, body = undefined) {
  message = {path: event}
  if (body !== undefined)
    message['body'] = body;
  messages += JSON.stringify(message) + "\n";
  if (message_timer == undefined) {
    elapsed = 1000;
    if (last_send !== undefined)
      elapsed = performance.now() - last_send;
    if (elapsed > 500) {
      send_messages();
    } else {
      delay = Math.max(1, Math.min(500 - elapsed, 500));
      message_timer = setTimeout(send_messages, delay);
    }
  }
};

// Incoming message handlers from the content script (relays messages from wptagent)
function onWptagentMessage(msg) {
  if (msg['msg'] == 'command' && msg['data'] != undefined) {
    message = JSON.parse(msg['data']);
  }
}
browser.runtime.onMessage.addListener(onWptagentMessage);


// Navigation handlers
browser.webNavigation.onBeforeNavigate.addListener(evt => {
  send_message('webNavigation.onBeforeNavigate', evt);
}, {
  url: [{schemes: ["http", "https"]}]}
);

browser.webNavigation.onCommitted.addListener(evt => {
  send_message('webNavigation.onCommitted', evt);
}, {
  url: [{schemes: ["http", "https"]}]}
);

browser.webNavigation.onDOMContentLoaded.addListener(evt => {
  send_message('webNavigation.onDOMContentLoaded', evt);
}, {
  url: [{schemes: ["http", "https"]}]}
);

browser.webNavigation.onCompleted.addListener(evt => {
  send_message('webNavigation.onCompleted', evt);
}, {
  url: [{schemes: ["http", "https"]}]}
);

browser.webNavigation.onErrorOccurred.addListener(evt => {
  send_message('webNavigation.onErrorOccurred', evt);
}, {
  url: [{schemes: ["http", "https"]}]}
);

// Request handlers
browser.webRequest.onBeforeRequest.addListener(details => {
  if (!details.url.startsWith(SERVER))
    send_message('webRequest.onBeforeRequest', details);
}, {urls: ["<all_urls>"]});

browser.webRequest.onSendHeaders.addListener(details => {
  if (!details.url.startsWith(SERVER))
    send_message('webRequest.onSendHeaders', details);
}, {urls: ["<all_urls>"]}, ["requestHeaders"]);

browser.webRequest.onHeadersReceived.addListener(details => {
  if (!details.url.startsWith(SERVER))
    send_message('webRequest.onHeadersReceived', details);
}, {urls: ["<all_urls>"]}, ["responseHeaders"]);

browser.webRequest.onResponseStarted.addListener(details => {
  if (!details.url.startsWith(SERVER))
    send_message('webRequest.onResponseStarted', details);
}, {urls: ["<all_urls>"]}, ["responseHeaders"]);

browser.webRequest.onBeforeRedirect.addListener(details => {
  if (!details.url.startsWith(SERVER))
    send_message('webRequest.onBeforeRedirect', details);
}, {urls: ["<all_urls>"]}, ["responseHeaders"]);

browser.webRequest.onCompleted.addListener(details => {
  if (!details.url.startsWith(SERVER))
    send_message('webRequest.onCompleted', details);
}, {urls: ["<all_urls>"]}, ["responseHeaders"]);

browser.webRequest.onErrorOccurred.addListener(details => {
  if (!details.url.startsWith(SERVER))
    send_message('webRequest.onErrorOccurred', details);
}, {urls: ["<all_urls>"]});

// Let wptagent know we started
send_message('wptagent.started');
