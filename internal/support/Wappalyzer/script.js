(async function() {
  %WAPPALYZER%;
  const json = %JSON%;
  var responseHeaders = %RESPONSE_HEADERS%;
  const wappalyzer = new Wappalyzer();
  wappalyzer.apps = json.apps;
  wappalyzer.categories = json.categories;
  wappalyzer.parseJsPatterns();
  wappalyzer.driver.document = document;
  let wptagentWappalyzer = null;

	const container = document.getElementById('wappalyzer-container');
	const url = wappalyzer.parseUrl(window.top.location.href);
	const hasOwn = Object.prototype.hasOwnProperty;

  wappalyzer.driver.log = (message, source, type) => {
  };

  function detectJs(chain) {
    const properties = chain.split('.');
    var value = properties.length ? window : null;
    for ( let i = 0; i < properties.length; i ++ ) {
      var property = properties[i];
      if ( value && value.hasOwnProperty(property) ) {
        value = value[property];
      } else {
        value = null;
        break;
      }
    }
    return typeof value === 'string' || typeof value === 'number' ? value : !!value;
  }
  
  async function getPageContent() {
    var e = document.getElementById('wptagentWappalyzer');
    if (e) {
      e.parentNode.removeChild(e);
    }
    // Get the environment variables
    var env = [];
    for ( let i in window ) {
      env.push(i);
    }
    // Get the script src URLs
    var scripts = Array.prototype.slice
      .apply(document.scripts)
      .filter(s => s.src)
      .map(s => s.src);
    // Find the JS variables
    const patterns = wappalyzer.jsPatterns || {};
    const js = {};
    for ( let appName in patterns ) {
      if ( patterns.hasOwnProperty(appName) ) {
        js[appName] = {};
        for ( let chain in patterns[appName] ) {
          if ( patterns[appName].hasOwnProperty(chain) ) {
            js[appName][chain] = {};
            for ( let index in patterns[appName][chain] ) {
              const value = detectJs(chain);
              if ( value && patterns[appName][chain].hasOwnProperty(index) ) {
                js[appName][chain][index] = value;
              }
            }
          }
        }
      }
    }
    // Run the analysis        
    const url = wappalyzer.parseUrl(window.top.location.href);
    await wappalyzer.analyze(url, {
      html: new window.XMLSerializer().serializeToString(document),
      headers: responseHeaders,
      env: env,
      js: js,
      scripts: scripts
    });
  }

  wappalyzer.driver.displayApps = detected => {
    var categories = {};
    var apps = {};
    if ( detected != null && Object.keys(detected).length ) {
      try {
        for (var app in detected) {
          try {
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
                categories[category] = '';
              }
              if (apps[app] === undefined) {
                apps[app] = '';
              }
              var app_name = app.trim();
              if (version && version.length) {
                let appVersion = version.trim();
                app_name += ' ' + appVersion;
                if (!apps[app].length || apps[app].indexOf(appVersion) === -1) {
                  if (apps[app].length) {
                    apps[app] += ',';
                  }
                  apps[app] += appVersion;
                }
              }
              if (categories[category].length) {
                categories[category] += ',';
              }
              categories[category] += app_name;
            }
          } catch (e) {
          }
        }
      } catch (e) {
      }
    }
    wptagentWappalyzer = JSON.stringify({
      categories: categories,
      apps: apps
    });
  };

  await getPageContent();
  return wptagentWappalyzer;
})();
