new PerformanceObserver((entryList) => {
    for (const entry of entryList.getEntries()) {
        try {
            let event = {
                name: entry.name,
                entryType: entry.entryType,
                startTime: entry['startTime'],
                size: entry['size'],
                url: entry['url'],
                id: entry['id'],
                loadTime: entry['loadTime'],
                renderTime: entry['renderTime'],
            };
            if (entry['element']) {
                event['element'] = {
                    nodeName: entry.element['nodeName'],
                    boundingRect: entry.element.getBoundingClientRect(),
                    outerHTML: entry.element.outerHTML,
                }
                if (entry.element['src']) {
                    event.element['src'] = entry.element.src;
                }
                if (entry.element['currentSrc']) {
                    event.element['currentSrc'] = entry.element.currentSrc;
                }
                try {
                    let style = window.getComputedStyle(entry.element);
                    if (style.backgroundImage && style.backgroundImage != 'none') {
                        event.element['background-image'] = style.backgroundImage;
                    }
                    if (style.content && style.content != 'none') {
                        event.element['content'] = style.content;
                    }
                } catch (err) {
                }
            }
            console.debug('wptagent_message:' + JSON.stringify({'name': 'perfentry', 'data': event}));
        } catch (err) {
        }
    }
}).observe({type: 'largest-contentful-paint', buffered: true});

new PerformanceObserver((entryList) => {
    for (const entry of entryList.getEntries()) {
        try {
            let event = {
                name: entry.name,
                entryType: entry.entryType,
                startTime: entry['startTime'],
                value: entry['value'],
                hadRecentInput: entry['hadRecentInput'],
                lastInputTime: entry['lastInputTime'],
            };
            if (entry['sources']) {
                event['sources'] = [];
                for (const source of entry.sources) {
                    let src = {
                        previousRect: source.previousRect,
                        currentRect: source.currentRect,
                    }
                    event.sources.push(src);
                }
            }
            console.debug('wptagent_message:' + JSON.stringify({'name': 'perfentry', 'data': event}));
        } catch (err) {
        }
    }
}).observe({type: 'layout-shift', buffered: true});
