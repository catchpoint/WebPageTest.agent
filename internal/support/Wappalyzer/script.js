(async function() {
  %WAPPALYZER%;
  const json = %JSON%;
  var responseHeaders = %RESPONSE_HEADERS%;
  Wappalyzer.setTechnologies(json.technologies);
  Wappalyzer.setCategories(json.categories);

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
    // Meta tags
    const meta = Array.from(document.querySelectorAll('meta')).reduce(
      (metas, meta) => {
        const key = meta.getAttribute('name') || meta.getAttribute('property')
        if (key) {
          metas[key.toLowerCase()] = [meta.getAttribute('content')]
        }
        return metas
      },
      {}
    )
    // Run the analysis        
    const detections = await Wappalyzer.analyze({
      url: window.top.location.href,
      html: new window.XMLSerializer().serializeToString(document),
      headers: responseHeaders,
      js: js,
      meta: meta,
      scripts: scripts
    });
    const detected = Wappalyzer.resolve(detections);

    // Parse the results into something useful
    let categories = {};
    let apps = {};
    let dedupe = {};

    for (let entry of detected) {
      try {
        if (entry) {
          const app = entry.name;
          const version = entry.version;
          for (let catEntry of entry.categories) {
            let category = catEntry.name;
            if (!category.length) {
              category = catEntry.slug;
            }
            if (category.length) {
              const key = category + ';' + app + ';' + version;
              if (dedupe[key] === undefined) {
                dedupe[key] = true;
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
            }
          }
        }
      } catch(e) {
      }
    }

    let wptResult = JSON.stringify({
      categories: categories,
      apps: apps
    });
    return wptResult;
  }

  return runWappalyzer();
})();
