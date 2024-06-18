#! /usr/bin/env python3
# Update the Chrome feature names from Chrome source
import json
import os
import re
import requests

SOURCES = {
    'blink': {
        'url': 'https://raw.githubusercontent.com/chromium/chromium/main/third_party/blink/public/mojom/use_counter/metrics/web_feature.mojom'
    },
    'css': {
        'url': 'https://raw.githubusercontent.com/chromium/chromium/main/third_party/blink/public/mojom/use_counter/metrics/css_property_id.mojom',
        'prefix': 'CSSProperty'
    }
}

pattern = re.compile(r'k([^ ]+) = (\d+)')
for name in SOURCES:
    path = os.path.join(os.path.abspath(os.path.dirname(__file__)), name + '.json')
    with open(path, 'rt', encoding='utf-8') as f:
        features = json.load(f)
    updated = False
    url = SOURCES[name]['url']
    prefix = SOURCES[name]['prefix'] if 'prefix' in SOURCES[name] else ''
    response = requests.get(url)
    for line in response.text.splitlines():
        match = pattern.search(line)
        if match is not None:
            value = match.group(1)
            key = match.group(2)
            if key not in features:
                features[key] = prefix + value
                updated = True
    if updated:
        with open(path, 'wt', encoding='utf-8') as f:
            json.dump(features, f, indent=4)
