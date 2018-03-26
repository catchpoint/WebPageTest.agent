(function() {
  %WAPPALYZER%
  const json = %JSON%;
  var responseHeaders = %RESPONSE_HEADERS%;
  const wappalyzer = new Wappalyzer();
  wappalyzer.apps = json.apps;
  wappalyzer.categories = json.categories;
  wappalyzer.parseJsPatterns();
  wappalyzer.driver.document = document;

	const container = document.getElementById('wappalyzer-container');
	const url = wappalyzer.parseUrl(top.location.href);
	const hasOwn = Object.prototype.hasOwnProperty;

  wappalyzer.driver.log = (message, source, type) => {
  };

  function getPageContent() {
    var e = document.getElementById('wptagentWappalyzer');
    if (e) {
      e.parentNode.removeChild(e);
    }
    var env = [];
    for ( let i in window ) {
      env.push(i);
    }
    var scripts = Array.prototype.slice
      .apply(document.scripts)
      .filter(s => s.src)
      .map(s => s.src);
    wappalyzer.analyze(url, {
      html: document.documentElement.innerHTML,
      headers: responseHeaders,
      env: env,
      scripts: scripts
    });
  }

  wappalyzer.driver.displayApps = detected => {
    var categories = {}
    if ( detected != null && Object.keys(detected).length ) {
      for (var app in detected) {
        if ( !hasOwn.call(detected, app) ) {
          continue;
        }
        var version = detected[app].version;
        for ( let i in wappalyzer.apps[app].cats ) {
          if ( !hasOwn.call(wappalyzer.apps[app].cats, i) ) {
            continue;
          }
          var category = wappalyzer.categories[wappalyzer.apps[app].cats[i]].name;
          if (categories[category] === undefined) {
            categories[category] = ''
          }
          var app_name = app;
          if (version && version.length) {
            app_name += ' ' + version;
          }
          if (categories[category].length) {
            categories[category] += ',';
          }
          categories[category] += app_name;
        }
      }
    }
    var e = document.getElementById('wptagentWappalyzer');
    if (!e && document.body) {
      e = document.createElement('div');
      e.id = 'wptagentWappalyzer';
      e.style = 'display: none;';
      document.body.appendChild(e);
    }
    if (e) {
      e.innerHTML = '';
      e.appendChild(document.createTextNode(JSON.stringify(categories)));
    }
  },

  getPageContent();
  return true;
})();
