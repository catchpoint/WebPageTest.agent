#!/bin/bash
git clone --depth 1 -b master https://github.com/wappalyzer/wappalyzer.git wappalyzer
rm internal/support/Wappalyzer/technologies/*.json
rm internal/support/Wappalyzer/*.json
rm internal/support/Wappalyzer/wappalyzer.js
cp wappalyzer/src/technologies/*.json internal/support/Wappalyzer/technologies/
cp wappalyzer/src/*.json internal/support/Wappalyzer/
cp wappalyzer/src/wappalyzer.js internal/support/Wappalyzer/
rm -rf wappalyzer
