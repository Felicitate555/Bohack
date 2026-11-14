// Exercise the same Canvas drawing code used by the browser renderer.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('app/scene_canvas.html', 'utf8');
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
let texts = [], lines = 0, endpoints = [], colors = [];
const ctx = new Proxy({
  createRadialGradient(){return {addColorStop(){}}},
  fillText(value){texts.push(String(value))},
  lineTo(a,b){lines++;endpoints.push([a,b])},
}, {get(o,k){return k in o ? o[k] : ()=>{}},set(o,k,v){if(k==='fillStyle'||k==='strokeStyle')colors.push(v);o[k]=v;return true}});
const sandbox = {document:{querySelector(){return {getContext(){return ctx}}}},window:{},Math};
vm.runInNewContext(script, sandbox);
function scene(visual, parameters){return {heading:'测试',key:'重点',detail:'说明',narration:'一句解释。',visual,spec:{parameters,animation:{keyframes:[0,.65]}}}}
async function draw(s,t=4){texts=[];lines=0;endpoints=[];await sandbox.window.renderFrame(s,0,1,t,4,[],[],960,540)}
(async()=>{
  await draw(scene('network',{count:23}));
  assert(texts.some(t=>t.includes('253 / 253')), '23 people should draw all 253 pairs');
  await draw(scene('network',{count:3,edges:[[0,1],[1,2]]}));
  assert(texts.some(t=>t.includes('2 / 2')), 'custom graph should use only the requested edges');
  await draw(scene('scattering',{}));
  assert(lines >= 9, 'scattering should draw rays');
  assert(texts.includes('光源') && texts.includes('观察者'), 'scattering should identify light source and observer');
  await draw(scene('scattering',{optical_path:'long'}));
  assert(texts.some(t=>t.includes('长光程')) && texts.some(t=>t.includes('直射光偏暖')), 'long path should explain warmer transmitted light');
  await draw(scene('growth',{principal:100,rate:10,periods:3}));
  assert(texts.includes('133.1'), '100 compounded at 10% for 3 periods should be 133.1');
  assert(texts.some(t=>t.includes('130')), 'simple interest comparison should end at 130');
  assert(texts.some(t=>t.includes('12.1')), 'third-period interest should be 12.1');
  assert(texts.some(t=>t.includes('计入本金')), 'the interest should be described as added to principal');
  for(const item of [scene('dots',{}),scene('growth',{principal:100,rate:10,periods:3}),scene('divergence',{}),scene('scattering',{}),scene('lorenz',{})]){
    await draw(item);
    assert(!texts.some(t=>/粒子数量为示意|曲线为示意，不代表历史实验数据|固定增长率的数学示意，不代表实际收益|光线与散射方向为示意/.test(t)), 'renderer should not add repetitive disclaimers');
  }
  const authored=scene('dots',{});authored.detail='用户输入：粒子数量为示意';await draw(authored);
  assert(texts.includes(authored.detail), 'user-authored text must remain visible');
  const event = {property:'translate_x',start:.2,end:.6,from:0,to:40,easing:'linear',beat_binding:true};
  assert.equal(sandbox.eventValue(event, 2.1, 10, [2.1,6.2]), 0);
  assert.equal(sandbox.eventValue(event, 6.2, 10, [2.1,6.2]), 40);
  const motion = {node:1,start:.2,end:.8,from_x:0,from_y:0,to_x:60,to_y:-30,easing:'linear',beat_binding:true};
  const before = sandbox.networkPositions(3, {}, [motion], 2.1, 10, [2.1,8.1]);
  const after = sandbox.networkPositions(3, {}, [motion], 8.1, 10, [2.1,8.1]);
  assert.equal(after[1][0] - before[1][0], 60);
  assert.equal(after[1][1] - before[1][1], -30);
  assert.equal(after[0][0], before[0][0], 'other nodes stay in place');
  let moving=scene('network',{count:3,edges:[[0,1]]});moving.spec.animation.node_motions=[motion];
  await draw(moving,4);
  const expected=sandbox.networkPositions(3,{},[motion],4,4,[])[1];
  assert(endpoints.some(([x,y])=>Math.abs(x-expected[0])<.001&&Math.abs(y-expected[1])<.001), 'edge endpoint follows moved node');
  const birthday=sandbox.distinctProbability(365,23);
  assert(Math.abs(birthday-.4927027657)<.0000001, '23 uniform independent draws should produce the correct distinct probability');
  assert.equal(sandbox.distinctProbability(2,3),0, 'more draws than categories must produce a collision');
  await draw(scene('distinct',{categories:365,draws:23}));
  assert(texts.some(t=>t.includes('49.27%')) && texts.some(t=>t.includes('50.73%')), 'show both calculated probabilities');
  assert(texts.some(t=>t.includes('独立')&&t.includes('等概率')), 'show the probability assumptions');
  sandbox.window.setStyle({palette:{accent:'#ff4400',text:'#f5f1e8',muted:'#9d927c',background:'#08090b',surface:'#101010',glow:'#242015'}});
  colors=[];await draw(scene('question',{}));
  assert(colors.includes('#ff4400'), 'renderer must consume the selected accent');
  sandbox.window.setStyle({});
  assert.equal(sandbox.captionAt(['短句','长长长长长长长长'],1.9,10), '短句');
  assert.equal(sandbox.captionAt(['短句','长长长长长长长长'],2.1,10), '长长长长长长长长');
  console.log('Network, scattering, growth and beat-bound event checks passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
