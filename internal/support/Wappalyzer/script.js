(async function() {
  %WAPPALYZER%;
  const wappalyzer_technologies = %TECHNOLOGIES%;
  const wappalyzer_categories = %CATEGORIES%;
  const cookies = %COOKIES%;
  const responseHeaders = %RESPONSE_HEADERS%;
  Wappalyzer.setTechnologies(wappalyzer_technologies);
  Wappalyzer.setCategories(wappalyzer_categories);

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
    const html = new window.XMLSerializer().serializeToString(document);
    let detections = await Wappalyzer.analyze({
      url: window.top.location.href,
      html: html,
      css: css,
      headers: responseHeaders,
      meta: meta,
      cookies: cookies,
      scriptSrc: scripts
    });
    let dom = getDom(Wappalyzer.technologies);
    detections = detections.concat(await analyzeDom(dom, Wappalyzer.technologies));
    detections = detections.concat(await analyzeJS(Wappalyzer.technologies));
    let resolved = Wappalyzer.resolve(detections);

    // Re-run the analysis for the subset of technologies that depend on something else
    const requires = Wappalyzer.requires.filter(({ name, technologies }) =>
      resolved.some(({ name: _name }) => _name === name)
    )
    let requires_tech = [];
    for (let entry of requires) {
      requires_tech = requires_tech.concat(entry['technologies']);
    }
    if (requires_tech.length) {
      detections = detections.concat(await Wappalyzer.analyze({
          url: window.top.location.href,
          html: html,
          css: css,
          headers: responseHeaders,
          meta: meta,
          cookies: cookies,
          scriptSrc: scripts
        }, requires_tech));
      dom = getDom(requires_tech);
      detections = detections.concat(await analyzeDom(dom, requires_tech));
      detections = detections.concat(await analyzeJS(requires_tech));
      resolved = Wappalyzer.resolve(detections);
    }

    // Parse the results into something useful
    let categories = {};
    let apps = {};
    let dedupe = {};

    for (let entry of resolved) {
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

  async function analyzeJS(js_technologies) {
      const js_tech = {
          technologies: js_technologies
            .filter(({ js }) => Object.keys(js).length)
            .map(({ name, js }) => ({ name, chains: Object.keys(js) })),
      };
      const { technologies } = js_tech;
      const js_data = {
        js: technologies.reduce((technologies, { name, chains }) => {
          chains.forEach((chain, index) => {
            const value = chain
              .split('.')
              .reduce(
                (value, method) =>
                  value &&
                  value instanceof Object &&
                  Object.prototype.hasOwnProperty.call(value, method)
                    ? value[method]
                    : '__UNDEFINED__',
                window
              )

            if (value !== '__UNDEFINED__') {
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
                js_technologies.find(({ name: _name }) => name === _name),
                'js',
                { [chain]: [value] }
              )
            })
          )
        );
      return detected;
  }

  function getDom(technologies) {
    return technologies
    .filter(({ dom }) => dom && dom.constructor === Object)
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
          // Continue
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
    }, [])  
  }

  async function analyzeDom(dom, technologies) {
    return Array.prototype.concat.apply(
      [],
      await Promise.all(
        dom.map(
          async ({
            name,
            selector,
            exists,
            text,
            property,
            attribute,
            value,
          }) => {
            await next()

            const technology = technologies.find(
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
              return Wappalyzer.analyzeManyToMany(technology, `dom.properties.${property}`, {
                [selector]: [value],
              })
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
    )
  }

  return runWappalyzer();
})();
