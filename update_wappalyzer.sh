#!/bin/bash
git clone --depth 1 -b main https://github.com/HTTPArchive/wappalyzer.git wappalyzer
rm internal/support/Wappalyzer/technologies/*.json
rm internal/support/Wappalyzer/*.json
rm internal/support/Wappalyzer/wappalyzer.js
cp wappalyzer/src/technologies/*.json internal/support/Wappalyzer/technologies/
cp wappalyzer/src/*.json internal/support/Wappalyzer/
cp wappalyzer/src/js/wappalyzer.js internal/support/Wappalyzer/
rm -rf wappalyzer
