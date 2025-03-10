const {
  glob,
  globSync,
  globStream,
  globStreamSync,
  Glob,
} = require('glob');
const esprima = require('esprima');
const fs = require('fs');
const crypto = require('crypto');

async function extractScripts() {
  if (process.argv.length > 3) {
    try {
      const filePath = process.argv[2];
      const scripts = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      let hashes = [];
      let count = 0;
      for (const id in scripts) {
        const script = scripts[id];
        const url = script['url'];
        const src = script['src'];
        const h = await parse(src, url);
        hashes.push(...h);
        count++;
      }
      console.log('Extracted ' + hashes.length + ' hashes from ' + count + ' scripts');

      if (hashes.length) {
        const outPath = process.argv[3];
        fs.writeFileSync(outPath, JSON.stringify(hashes));
      }
    } catch(err) {
      console.error(err);
    }
  }
}

async function parse(src, url) {
  try {
    const options = { tolerant: true, range: true, comment: true };
    // Try parsing it as a JS file
    try {
      const parsed = esprima.parseScript(src, options);
      return await processScript(parsed, src, url);
    } catch (err) {
    }

    // Try parsing it as a JS module
    try {
      const parsed = esprima.parseModule(src, options);
      return await processScript(parsed, src, url);
    } catch (err) {
    }
  } catch (err) {
    console.error(err);
  }
  return [];
}

function generateSHA256(data) {
  const hash = crypto.createHash('sha256');
  hash.update(data);
  return hash.digest('hex');
}

async function processScript(parsed, src, url) {
  let chunks = []

  function processNodes(nodes, chunk_type) {
    for (const node of nodes) {
      const len = node.range[1] - node.range[0];
      if (len > 250) {
        let chunk = {'url': url,
                     'src': src.substring(node.range[0], node.range[1]),
                     'type': chunk_type};
        chunk['hash'] = generateSHA256(chunk['src']);
        chunks.push(chunk)
      }
    }
  }
  processNodes(parsed.comments, 'comment');
  processNodes(parsed.body, 'script');

  return chunks;
}

extractScripts();