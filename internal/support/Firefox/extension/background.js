var SERVER = "http://127.0.0.1:8888/";
var messages = '';
var message_timer = undefined;
var last_send = undefined;
var blockingWebRequest = false;
var block = [];
var block_domains = [];
var block_domains_except = [];
var headers = {};
var overrideHosts = {};

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

var log = function(message) {
  message_headers = new Headers({
    "Content-Type": "application/json",
    "Content-Length": message.length.toString()
  });
  fetch(SERVER + 'log',
        {method: 'POST', headers: message_headers, body: message});
};

function get_domain(url) {
  var domain;
  if (url.indexOf("://") > -1) {
    domain = url.split('/')[2];
  } else {
    domain = url.split('/')[0];
  }
  domain = domain.split(':')[0];
  domain = domain.split('?')[0];
  return domain;
}

function blockRequest(details) {
  var ret = {cancel: false}
  if (!details.url.startsWith(SERVER)) {
    var domain = get_domain(details.url);
    for (var i = 0; i < block.length; i++) {
      if (details.url.indexOf(block[i]) !== -1) {
        ret.cancel = true;
        break;
      }
    }
    if (!ret.cancel && block_domains.length > 0) {
      for (var i = 0; i < block_domains.length; i++) {
        if (domain == block_domains[i]) {
          ret.cancel = true;
          break;
        }
      }
    }
    if (!ret.cancel && block_domains_except.length > 0) {
      if (!block_domains_except.includes(domain)) {
        ret.cancel = true;
      }
    }
  }
  return ret;
}

function addHeaders(details) {
  if (!details.url.startsWith(SERVER)) {
    for (name in headers) {
      for (var i = 0; i < details.requestHeaders.length; ++i) {
        if (details.requestHeaders[i].name === name) {
          details.requestHeaders.splice(i, 1);
          break;
        }
      }
      details.requestHeaders.push({'name': name, 'value': headers[name]})
    }
    var url = new URL(details.url);
    for (host in overrideHosts) {
      if (host == url.hostname) {
        for (var i = 0; i < details.requestHeaders.length; ++i) {
          if (details.requestHeaders[i].name === 'Host') {
            details.requestHeaders.splice(i, 1);
            break;
          }
        }
        details.requestHeaders.push({'name': 'Host', 'value': overrideHosts[host]})
        details.requestHeaders.push({'name': 'x-Host', 'value': host})
      }
    }
  }
  return {requestHeaders: details.requestHeaders};
}

var installBlockingHandler = function() {
  if (!blockingWebRequest) {
    blockingWebRequest = true;
    browser.webRequest.onBeforeRequest.addListener(blockRequest, {urls: ["<all_urls>"]}, ["blocking"]);
    browser.webRequest.onBeforeSendHeaders.addListener(addHeaders, {urls: ["<all_urls>"]}, ["blocking", "requestHeaders"]);
  }
};

// Get the config from wptagent
fetch(SERVER + 'config').then(function(response) {
  if (response.ok) {
    response.json().then(function(data) {
      if (data['block'] != undefined) {
        block = data['block'];
      }
      if (data['block_domains'] != undefined) {
        block_domains = data['block_domains'];
      }
      if (data['block_domains_except'] != undefined) {
        block_domains_except = data['block_domains_except'];
      }
      if (data['headers'] != undefined) {
        headers = data['headers'];
      }
      if (data['overrideHosts'] != undefined) {
        overrideHosts = data['overrideHosts'];
      }
      if (data['cookies'] != undefined) {
        for (var i = 0; i < data['cookies'].length; i++) {
          try {
            browser.cookies.set(data['cookies'][i]);
          } catch(e) {
          }
        }
      }
      if (block.length ||
          block_domains.length ||
          block_domains_except.length ||
          Object.keys(headers).length ||
          Object.keys(overrideHosts).length) {
        installBlockingHandler();
      }
      // Let wptagent know we started
      send_message('wptagent.started');
    });
  }
});

// Navigation handlers
browser.webNavigation.onBeforeNavigate.addListener(details => {
  if (!details.url.startsWith(SERVER))
    send_message('webNavigation.onBeforeNavigate', details);
}, {
  url: [{schemes: ["http", "https"]}]}
);

browser.webNavigation.onCommitted.addListener(details => {
  if (!details.url.startsWith(SERVER))
    send_message('webNavigation.onCommitted', details);
}, {
  url: [{schemes: ["http", "https"]}]}
);

browser.webNavigation.onDOMContentLoaded.addListener(details => {
  if (!details.url.startsWith(SERVER))
    send_message('webNavigation.onDOMContentLoaded', details);
}, {
  url: [{schemes: ["http", "https"]}]}
);

browser.webNavigation.onCompleted.addListener(details => {
  if (!details.url.startsWith(SERVER))
    send_message('webNavigation.onCompleted', details);
}, {
  url: [{schemes: ["http", "https"]}]}
);

browser.webNavigation.onErrorOccurred.addListener(details => {
  if (!details.url.startsWith(SERVER))
    send_message('webNavigation.onErrorOccurred', details);
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
