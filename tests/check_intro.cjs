// Deterministic animation/controls check. No browser install, screenshots, or video.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../intro/lakers-intro.html'),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const elements={};
for (const match of html.matchAll(/\bid="([^"]+)"/g)) {
  elements[match[1]]={value:'0',textContent:'',clientWidth:960,style:{setProperty(key,value){this[key]=value;}},
    events:{},addEventListener(type,handler){this.events[type]=handler;},setAttribute(){},decode(){return Promise.resolve();}};
}
const context={document:{getElementById:id=>elements[id],fonts:{ready:Promise.resolve()},addEventListener(){}},
  performance:{now:()=>0},requestAnimationFrame:()=>1,cancelAnimationFrame(){},
  ResizeObserver:class {observe(){}},matchMedia:()=>({matches:false})};
vm.createContext(context);vm.runInContext(script,context);
assert.equal(elements.scene.style['--scale'],0.5);
context.pause();context.renderFrame(0.5);
assert.equal(elements['opening-word'].style.opacity,1);
assert.equal(elements['opening-city'].style.opacity,0);
assert.equal(elements['logo-wrap'].style.opacity,0);
context.renderFrame(1.6);
assert.equal(elements['opening-word'].style.opacity,0);
assert.equal(elements['opening-city'].style.opacity,1);
assert.equal(elements['logo-wrap'].style.opacity,0);
context.renderFrame(2.15);
assert.ok(elements['opening-city'].style.opacity>0 && elements['opening-city'].style.opacity<1);
assert.ok(elements['logo-wrap'].style.opacity>0 && elements['logo-wrap'].style.opacity<1);
context.renderFrame(2.7);
assert.equal(elements['opening-city'].style.opacity,0);
assert.equal(elements['logo-wrap'].style.opacity,1);
context.renderFrame(4);
assert.equal(elements.time.textContent,'4.00 / 4.00 秒');
assert.equal(elements['logo-wrap'].style.transform,'scale(1)');
for (let frame=0;frame<=100;frame++) {
  context.renderFrame(frame/25);
  for (const id of ['opening-word','opening-city','logo-wrap']) {
    assert.ok(elements[id].style.opacity>=0 && elements[id].style.opacity<=1);
    assert.ok(!String(elements[id].style.transform).includes('NaN'));
    if(id !== 'logo-wrap') assert.equal(elements[id].style.transform,undefined,'Text must stay fixed during fades');
  }
}
assert.ok(!/id="(?:basketball|ball-photo|logo-ball|ball-aligned)"/.test(html));
assert.ok(!html.includes('clipPath'));
elements.replay.events.click();assert.equal(elements.time.textContent,'0.00 / 4.00 秒');
elements.seek.value='2.5';elements.seek.events.input();
assert.equal(elements.time.textContent,'2.50 / 4.00 秒');assert.equal(elements.play.textContent,'播放');
assert.ok(!/id="(?:title|player|orbit|word|ribbon)"|class="(?:court|weave|team|series)"/.test(html));
console.log('PASS: timeline states, 101 computed states, text sequence, full logo reveal, replay, scrubbing. No screenshots/video generated.');
