var SERVER = "http://127.0.0.1:8888/";
var blockingWebRequest = false;
var block = [];
var block_domains = [];
var block_domains_except = [];
var headers = {};
var overrideHosts = {};

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
      for (var i = 0; i < block_domains_except.length; i++) {
        if (domain != block_domains_except[i]) {
          ret.cancel = true;
          break;
        }
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
        details.requestHeaders.push({'name': 'x-host', 'value': host})
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

// Get the config from wptagent when the config.html page is loaded
browser.runtime.onMessage.addListener(function(data) {
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
        var cookie = data['cookies'][i];
        cookie["expirationDate"] = Date.now() / 1000 + (60 * 60 * 24);
        console.log(JSON.stringify(cookie));
        browser.cookies.set(cookie, function(){});
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
});
