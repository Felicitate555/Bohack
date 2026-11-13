// Exercise the same Canvas drawing code used by the browser renderer.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('app/scene_canvas.html', 'utf8');
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
let texts = [], lines = 0;
const ctx = new Proxy({
  createRadialGradient(){return {addColorStop(){}}},
  fillText(value){texts.push(String(value))},
  lineTo(){lines++},
}, {get(o,k){return k in o ? o[k] : ()=>{}},set(o,k,v){o[k]=v;return true}});
const sandbox = {document:{querySelector(){return {getContext(){return ctx}}}},window:{},Math};
vm.runInNewContext(script, sandbox);
function scene(visual, parameters){return {heading:'测试',key:'重点',detail:'说明',narration:'一句解释。',visual,spec:{parameters,animation:{keyframes:[0,.65]}}}}
async function draw(s){texts=[];lines=0;await sandbox.window.renderFrame(s,0,1,4,4,[],[],960,540)}
(async()=>{
  await draw(scene('network',{count:23}));
  assert(texts.some(t=>t.includes('253 / 253')), '23 people should draw all 253 pairs');
  await draw(scene('network',{count:3,edges:[[0,1],[1,2]]}));
  assert(texts.some(t=>t.includes('2 / 2')), 'custom graph should use only the requested edges');
  await draw(scene('scattering',{}));
  assert(lines >= 9 && texts.includes('光线与散射方向为示意'), 'scattering should draw rays and mark illustration');
  await draw(scene('growth',{principal:100,rate:10,periods:3}));
  assert(texts.includes('133.1'), '100 compounded at 10% for 3 periods should be 133.1');
  assert(texts.some(t=>t.includes('130')), 'simple interest comparison should end at 130');
  assert(texts.some(t=>t.includes('12.1')), 'third-period interest should be 12.1');
  assert(texts.some(t=>t.includes('计入本金')), 'the interest should be described as added to principal');
  const event = {property:'translate_x',start:.2,end:.6,from:0,to:40,easing:'linear',beat_binding:true};
  assert.equal(sandbox.eventValue(event, 2.1, 10, [2.1,6.2]), 0);
  assert.equal(sandbox.eventValue(event, 6.2, 10, [2.1,6.2]), 40);
  assert.equal(sandbox.captionAt(['短句','长长长长长长长长'],1.9,10), '短句');
  assert.equal(sandbox.captionAt(['短句','长长长长长长长长'],2.1,10), '长长长长长长长长');
  console.log('Network, scattering, growth and beat-bound event checks passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
