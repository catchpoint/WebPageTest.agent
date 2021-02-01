(async function() {
  %WAPPALYZER%;
  const json = %JSON%;
  var responseHeaders = %RESPONSE_HEADERS%;
  Wappalyzer.setTechnologies(json.technologies);
  Wappalyzer.setCategories(json.categories);
  let wptagentWappalyzer = null;

  async function runWappalyzer() {
    // Get the script src URLs
    var scripts = Array.prototype.slice
      .apply(document.scripts)
      .filter(s => s.src)
      .map(s => s.src);
    // Find the JS variables
    const patterns = Wappalyzer.jsPatterns || {};
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
    const detections = Wappalyzer.analyze({
      url: window.top.location.href,
      html: new window.XMLSerializer().serializeToString(document),
      headers: responseHeaders,
      js: js,
      scripts: scripts
    });
    const detected = Wappalyzer.resolve(detections);

    // Parse the results into something useful
    let categories = {};
    let apps = {};

    for (let index in detected) {
      try {
        const entry = detected[index];
        const app = entry.name.trim();
        const version = entry.version;
        for (let catIndex in entry.categories) {
          const catEntry = entry.categories[catIndex];
          const category = catEntry.name.trim();
          if (categories[category] === undefined) {
            categories[category] = '';
          }
          if (apps[app] === undefined) {
            apps[app] = '';
          }
          let app_name = app;
          if (version && version.length) {
            app_name += ' ' + version;
            if (!apps[app].length || apps[app].indexOf(version) === -1) {
              if (apps[app].length) {
                apps[app] += ',';
              }
              apps[app] += version;
            }
          }
          if (categories[category].length) {
            categories[category] += ',';
          }
          categories[category] += app_name;
        }
      } catch(e) {
      }
    }

    let wptResult = JSON.stringify({
      categories: categories,
      apps: apps
    });
    console.log(wptResult);
    return wptResult;
  }

  return runWappalyzer();
})();
