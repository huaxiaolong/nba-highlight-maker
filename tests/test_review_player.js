// Run with: node tests/test_review_player.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../web/nba-review.html'), 'utf8');
const fields = new Map();
const $ = id => {
  if (!fields.has(id)) fields.set(id, {value: '', disabled: false, hidden: false, textContent: ''});
  return fields.get(id);
};
const options = new Map();
$('view').querySelector = selector => {
  if (!options.has(selector)) options.set(selector, {});
  return options.get(selector);
};
const video = {
  src: '', currentTime: 2, duration: 100,
  pause() {}, load() {}, play() { throw new Error('Final video must not play a source interval'); },
  removeAttribute() {this.src = '';}, getAttribute() {return this.src;},
};
const context = vm.createContext({
  $, video, busy: false, dirty: false, rangePlay: false, previewOffset: 0, seekTarget: 0,
  playerSelectionDirty: false,
  active: {source: '/source.mp4', start: '00:10', preview: '/preview.mp4', preview_url: '/preview'},
  data: {sources: [{path: '/source.mp4', url: '/source'}], publication: {
    state: 'ready', title: 'title', description: 'description', directory: '/output',
    video: {url: '/final', path: '/output/highlights.mp4'},
  }},
  text(id, value) {$(id).textContent = value;}, notify() {},
});
function load(start, end) {
  const a = html.indexOf(start), b = html.indexOf(end, a);
  assert(a >= 0 && b > a, 'Player function must exist');
  vm.runInContext(html.slice(a, b), context);
}
load('function time(', 'function notify(');
load('function updateDouyinTitle(', 'function publication(');
load('function selectedRows(', 'function checkedPlayers(');
context.renderOpening=()=>{};
$('opening-list').querySelectorAll=()=>[];
load('function openingUnavailable(', 'function renderOpening(');
load('function updateControls(', 'function selectRow(');
load('function loadVideo(', 'video.onloadedmetadata=');
load('function currentTime(', 'function nudge(');
load('function playRange(', 'video.ontimeupdate=');
$('source').value = '/source.mp4';
$('start').value = '00:10';
$('end').value = '00:13';
$('view').value = 'final';
context.loadVideo();
assert.equal(video.src, '/final');
assert.equal(context.previewOffset, 0);
assert.equal($('editor').disabled, true);
assert.equal($('playback-notice').hidden, true);
context.data.publication.state = 'stale';
context.loadVideo();
assert.equal($('playback-notice').hidden, false, 'An outdated final video must still show a warning');
context.data.publication.state = 'ready';
assert.equal($('play-range').disabled, true);
context.setBound('start');
context.playRange();
assert.equal($('start').value, '00:10', 'Final video time must never replace a source boundary');
$('view').value = 'source';
context.loadVideo(10);
assert.equal(video.src, '/source');
assert.equal(context.seekTarget, 10);
assert.equal($('editor').disabled, false);
context.setBound('start');
assert.equal($('start').value, '00:02.000');
$('view').value = 'preview';
context.loadVideo(10);
assert.equal(video.src, '/preview');
assert.equal(context.previewOffset, 10);
context.setBound('start');
assert.equal($('start').value, '00:12.000', 'Clip previews must translate back to source time');
console.log('Review player: final playback, source bounds, and preview offsets passed');

load('function updateExportCommand(', 'function publication(');
context.data.export_command = "python3 scripts/nba_highlights.py export --review '/tmp/with space/review.csv'";
for (const opening of ['', '/tmp/first clip.mp4', "/tmp/O'Brien $(echo unsafe).mov\n/tmp/second.mp4"]) {
  for (const watermark of [false, true]) {
    context.data.prepend = opening ? opening.split('\n') : [];
    context.data.opening_files = context.data.prepend.map(path=>({path}));
    $('export-watermark').checked = watermark;
    context.updateExportCommand();
    const paths = opening.split('\n').filter(Boolean);
    const expected = paths.length ? ' --prepend ' + paths.map(p => "'" + p.replace(/'/g, "'\\''") + "'").join(' ') : '';
    assert.equal($('export-command').textContent, context.data.export_command + expected + (watermark ? '' : ' --no-watermark'));
    if (paths.length) {
      const {execFileSync} = require('node:child_process');
      const command = $('export-command').textContent.replace(context.data.export_command, 'python3 -c \'import json,sys; print(json.dumps(sys.argv[1:]))\'');
      assert.deepEqual(JSON.parse(execFileSync('/bin/sh', ['-c', command], {encoding: 'utf8'})), ['--prepend', ...paths, ...(watermark ? [] : ['--no-watermark'])]);
    }
  }
}
context.data.prepend = [];
console.log('Review export command: optional opening paths and shell quoting passed');

load('function moveOpening(', 'async function saveOpening(');
let savedOpening;
context.saveOpening=paths=>{savedOpening=[...paths];context.data.prepend=[...paths];};
context.data.prepend=['first','second','third'];
context.moveOpening('third',-1);
assert.deepEqual(savedOpening,['first','third','second']);
context.moveOpening('first',1);
assert.deepEqual(savedOpening,['third','first','second']);
savedOpening=null;context.moveOpening('third',-1);assert.equal(savedOpening,null);
context.data.opening_files=[{path:'first'}];
assert.equal(context.openingUnavailable(),true);
context.data.prepend=['first'];assert.equal(context.openingUnavailable(),false);
context.data.prepend=[];
console.log('Opening arrangement: order, bounds, missing file guard passed');

const copiedTitles = [];
context.copy = title => copiedTitles.push(title);
load("$('copy-title-douyin').onclick=", "$('copy-description').onclick=");
for (const title of ['', '标'.repeat(30), '标'.repeat(31), '🏀'.repeat(30), '🏀'.repeat(31)]) {
  context.data.publication.douyin_title = title;
  context.updateDouyinTitle();
  const valid = !!title && [...title].length <= 30;
  assert.equal($('upload-title-douyin').value, title);
  assert.equal($('copy-title-douyin').disabled, !valid);
  assert($('douyin-title-count').textContent.startsWith(`${[...title].length}/30`));
  const before = copiedTitles.length;
  $('copy-title-douyin').onclick();
  assert.equal(copiedTitles.length, before + Number(valid));
  if (valid) assert.equal(copiedTitles.at(-1), title);
}
context.data.publication.douyin_title = '湖人集锦';
context.busy = true;
context.updateDouyinTitle();
assert.equal($('copy-title-douyin').disabled, true);
const beforeCopy = copiedTitles.length;
$('copy-title-douyin').onclick();
assert.equal(copiedTitles.length, beforeCopy);
console.log('Douyin title: empty, 30/31 characters, Unicode, copy guard, and busy state passed');

context.data.publication.covers = [];
context.document = {createElement: () => ({})};
$('covers').replaceChildren = () => {};
$('covers').append = () => {};
load('function publication(', 'function stats(');
for (const state of ['ready', 'stale', 'missing', 'incomplete', 'unverified']) {
  context.data.publication.state = state;
  context.publication();
  assert.equal($('publication-notice').hidden, state === 'ready');
  if (state !== 'ready') assert($('publication-notice').textContent.length > 0);
}
console.log('Review notices: routine text hidden, outdated and unavailable assets remain visible');

context.busy = false;
context.data.selected_players = ['201939'];
context.data.rows = [{player:'201939'}, {player:'100'}];
context.data.publication.export_players = ['doncic'];
assert.equal(context.selectedRows().length, 1);
assert.equal(context.selectionMatchesExport(), false);
context.data.publication.state = 'ready';
context.publication();
assert.equal($('publication-notice').hidden, false, 'Changing selected players must mark old publication assets stale');
context.updateExportCommand();
assert($('export-command').textContent.includes(' --players 201939'));
context.playerSelectionDirty = true;
context.updateControls();
assert.equal($('copy-export').disabled, true, 'Unapplied player choices must not be exported');
context.playerSelectionDirty = false;
context.data.publication.export_players = ['201939'];
assert.equal(context.selectionMatchesExport(), true);
context.data.selected_players = [];
context.updateControls();
assert.equal($('copy-export').disabled, true);
console.log('Player selection: filtering, export scope, unapplied/empty guards, and stale assets passed');
