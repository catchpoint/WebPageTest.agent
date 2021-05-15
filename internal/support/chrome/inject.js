var WptAgentFlatten = function(object) {
    let contents = {}
    const ignore = ['innerText', 'outerText', 'innerHTML','textContent', 'baseURI', 'namespaceURI'];
    for (const key in object) {
        if (typeof(object[key]) === 'object') {
            if (Array.isArray(object[key])) {
                let values = [];
                for (const e of object[key]) {
                    if (typeof(e) === 'object') {
                        values.push(WptAgentFlatten(e));
                    } else if (typeof(e) !== 'function') {
                        values.push(e);
                    }
                }
                contents[key] = values;
            } else if (['element', 'node', 'currentRect', 'previousRect'].indexOf(key) >= 0) {
                contents[key] = WptAgentFlatten(object[key]);
                if (typeof object[key]['getBoundingClientRect'] === 'function') {
                    contents[key]['boundingRect'] =  object[key].getBoundingClientRect();
                }
                if (key == 'element') {
                    try {
                        let style = window.getComputedStyle(object[key]);
                        if (style.backgroundImage && style.backgroundImage != 'none') {
                            contents[key]['background-image'] = style.backgroundImage;
                        }
                    } catch (err) {
                    }
                }
            }
        } else if (typeof(object[key]) === 'string') {
            if (object[key].length > 0 && ignore.indexOf(key) === -1) {
                if (object[key].substring(0,4) == 'http') {
                    contents[key] = object[key];
                } else {
                    contents[key] = object[key].substring(0,200);
                }
            }
        } else if (typeof(object[key]) !== 'function') {
            if (!key.match(/^[A-Z_]+$/)) {
                contents[key] = object[key];
            }
        }
    }
    return contents;
}

var WptAgentReportPerformanceTiming = function(entryList){
    for (const entry of entryList.getEntries()) {
        console.debug('wptagent_message:' + JSON.stringify({'name': 'perfentry', 'data': WptAgentFlatten(entry)}));
    }
}

var wptagent_perf_observer = new PerformanceObserver((entryList) => {
    WptAgentReportPerformanceTiming(entryList);
});
wptagent_perf_observer.observe({type: 'largest-contentful-paint', buffered: true});
wptagent_perf_observer.observe({type: 'layout-shift', buffered: true});
wptagent_perf_observer.observe({type: 'paint', buffered: true});
wptagent_perf_observer.observe({type: 'element', buffered: true});
