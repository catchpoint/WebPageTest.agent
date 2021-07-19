(async function() {
  %WAPPALYZER%;
  const json = %JSON%;
  var responseHeaders = %RESPONSE_HEADERS%;
  Wappalyzer.setTechnologies(json.technologies);
  Wappalyzer.setCategories(json.categories);

  async function runWappalyzer() {
    // CSS rules
    let css = []
    try {
      for (const sheet of Array.from(document.styleSheets)) {
        for (const rules of Array.from(sheet.cssRules)) {
          css.push(rules.cssText)
        }
      }
    } catch (error) {
      // Continue
    }
    css = css.join('\n')
      
    // Script tags
    const scripts = Array.from(document.scripts)
      .filter(({ src }) => src)
      .map(({ src }) => src)
      .filter((script) => script.indexOf('data:text/javascript;') !== 0)    
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
      css: css,
      headers: responseHeaders,
      meta: meta,
      scripts: scripts
    });
    const dom_detections = await analyzeDom(Wappalyzer.technologies);
    const js_detections = await analyzeJS();
    const all_detections = detections.concat(dom_detections).concat(js_detections);
    const detected = Wappalyzer.resolve(all_detections);

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

  async function analyzeJS() {
      const js_tech = {
          technologies: Wappalyzer.technologies
            .filter(({ js }) => Object.keys(js).length)
            .map(({ name, js }) => ({ name, chains: Object.keys(js) })),
      };
      const { technologies } = js_tech;
      const js_data = {
          js: technologies.reduce((technologies, { name, chains }) => {
              chains.forEach((chain) => {
                const value = chain
                  .split('.')
                  .reduce(
                    (value, method) =>
                      value &&
                      value instanceof Object &&
                      Object.prototype.hasOwnProperty.call(value, method)
                        ? value[method]
                        : undefined,
                    window
                  )
  
                if (value !== undefined) {
                  technologies.push({
                    name,
                    chain,
                    value:
                      typeof value === 'string' || typeof value === 'number'
                        ? value
                        : !!value,
                  })
                }
              })
  
              return technologies
            }, [])
      };
      const detected = Array.prototype.concat.apply(
          [],
          await Promise.all(
            js_data.js.map(async ({ name, chain, value }) => {
              await next()
              return Wappalyzer.analyzeManyToMany(
                Wappalyzer.technologies.find(({ name: _name }) => name === _name),
                'js',
                { [chain]: [value] }
              )
            })
          )
        );
      return detected;
  }

  async function analyzeDom(technologies) {
      // DOM
      const dom = technologies
        .filter(({ dom }) => dom && dom.constructor === Object)
        .map(({ name, dom }) => ({ name, dom }))
        .reduce((technologies, { name, dom }) => {
          const toScalar = (value) =>
            typeof value === 'string' || typeof value === 'number'
              ? value
              : !!value
  
          Object.keys(dom).forEach((selector) => {
            let nodes = []
  
            try {
              nodes = document.querySelectorAll(selector)
            } catch (error) {
              // Ignore
            }
  
            if (!nodes.length) {
              return
            }
  
            dom[selector].forEach(({ exists, text, properties, attributes }) => {
              nodes.forEach((node) => {
                if (exists) {
                  technologies.push({
                    name,
                    selector,
                    exists: '',
                  })
                }
  
                if (text) {
                  const value = node.textContent.trim()
  
                  if (value) {
                    technologies.push({
                      name,
                      selector,
                      text: value,
                    })
                  }
                }
  
                if (properties) {
                  Object.keys(properties).forEach((property) => {
                    if (Object.prototype.hasOwnProperty.call(node, property)) {
                      const value = node[property]
  
                      if (typeof value !== 'undefined') {
                        technologies.push({
                          name,
                          selector,
                          property,
                          value: toScalar(value),
                        })
                      }
                    }
                  })
                }
  
                if (attributes) {
                  Object.keys(attributes).forEach((attribute) => {
                    if (node.hasAttribute(attribute)) {
                      const value = node.getAttribute(attribute)
  
                      technologies.push({
                        name,
                        selector,
                        attribute,
                        value: toScalar(value),
                      })
                    }
                  })
                }
              })
            })
          })
  
          return technologies
        }, []);
  
      const detected = Array.prototype.concat.apply(
        [],
        await Promise.all(
          dom.map(
            async (
              { name, selector, exists, text, property, attribute, value },
              index
            ) => {
              await next()
  
              const technology = Wappalyzer.technologies.find(
                ({ name: _name }) => name === _name
              )
  
              if (typeof exists !== 'undefined') {
                return Wappalyzer.analyzeManyToMany(technology, 'dom.exists', {
                  [selector]: [''],
                })
              }
  
              if (typeof text !== 'undefined') {
                return Wappalyzer.analyzeManyToMany(technology, 'dom.text', {
                  [selector]: [text],
                })
              }
  
              if (typeof property !== 'undefined') {
                return Wappalyzer.analyzeManyToMany(
                  technology,
                  `dom.properties.${property}`,
                  {
                    [selector]: [value],
                  }
                )
              }
  
              if (typeof attribute !== 'undefined') {
                return Wappalyzer.analyzeManyToMany(
                  technology,
                  `dom.attributes.${attribute}`,
                  {
                    [selector]: [value],
                  }
                )
              }
  
              return []
            }
          )
        )
      );
  
      return detected;
  }

  return runWappalyzer();
})();
