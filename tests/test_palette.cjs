const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('web/app.js','utf8');
const match=source.match(/function paletteContrast\([\s\S]*?\n}/);
assert(match,'palette contrast helper must be available');
const context={Math};vm.runInNewContext(match[0],context);
assert(context.paletteContrast('#ffffff','#000000')>20);
assert(context.paletteContrast('#ffffff','#f0f0f0')<1.5);
console.log('palette contrast checks passed');
