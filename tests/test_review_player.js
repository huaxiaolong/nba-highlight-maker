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
context.updateAnalyzeCommand=()=>{};
$('opening-list').querySelectorAll=()=>[];
load('function openingUnavailable(', 'function renderOpening(');
load('function updateControls(', 'function selectRow(');
load('function loadVideo(', 'video.onloadedmetadata=');
load('function currentTime(', 'function nudge(');
load('function playRange(', 'video.ontimeupdate=');
$('source').value = '/source.mp4';
$('start').value = '00:10';
$('end').value = '00:13';
$('view').value = 'source';
context.active.start='';
context.active.preview_url='';
context.loadVideo(NaN);
assert.equal(video.src,'','An unlocated event must not silently open the whole recording at zero');
assert.equal($('locate-source').hidden,false);
assert($('empty-video').textContent.includes('尚未定位'));
context.busy=true;
context.loadVideo(NaN);
assert.equal($('locate-source').disabled,true);
context.busy=false;
context.updateControls();
assert.equal($('locate-source').disabled,false,'Manual location must become available when the board finishes loading');
context.loadVideo(0);
assert.equal(video.src,'/source','An explicit manual-location action may open the source at zero');
assert.equal(context.seekTarget,0);
context.active.start='00:10';
context.active.preview_url='/preview';
context.loadVideo(10);
assert.equal($('locate-source').hidden,true);
context.active.start='00:00';
context.loadVideo(0);
assert.equal(video.src,'/source','A real start at zero remains a valid located event');
assert.equal($('locate-source').hidden,true);
context.active.start='00:10';
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

const originalLoadVideo=context.loadVideo;
context.document={createElement:()=>({})};
context.names={reaves:'Austin Reaves'};
context.kinds={score:'得分'};
context.labels={pending:'待确认'};
context.option=()=>{};
context.draw=()=>{};
for(const id of ['identity','source','candidate']){
  $(id).replaceChildren=()=>{};
  $(id).append=()=>{};
}
load('function selectRow(', 'function loadVideo(');
let requestedTarget;
context.loadVideo=target=>{requestedTarget=target};
const unlocated={player:'reaves',kind:'score',period_label:'Q1',clock_label:'10:31',event_id:'15',status:'pending',source:'/source.mp4',start:'',end:'',options:[]};
context.selectRow(unlocated);
assert(Number.isNaN(requestedTarget),'Selecting an unlocated row must preserve the missing timestamp');
context.selectRow({...unlocated,start:'00:10',end:'00:13'});
assert.equal(requestedTarget,10);
context.loadVideo=originalLoadVideo;
context.active={source:'/source.mp4',start:'00:10',preview:'/preview.mp4',preview_url:'/preview'};
console.log('Unlocated events: selection preserves missing time; manual source playback and real zero work');

load('video.onplay=', 'video.onerror=');
const originalPause=video.pause;
let autoPauses=0;
video.pause=()=>{autoPauses++;video.paused=true};
function startPlayback(mode,position,start='00:10',end='00:13',offset=0){
  $('view').value=mode;$('start').value=start;$('end').value=end;
  $('loop').checked=false;context.previewOffset=offset;
  video.currentTime=position;video.paused=false;context.rangePlay=false;
  video.onplay();
}
startPlayback('source',10);
video.currentTime=12.9;video.ontimeupdate();
assert.equal(autoPauses,0);
video.currentTime=13.2;video.ontimeupdate();
assert.equal(autoPauses,1,'Native play must stop at the marked end');
assert.equal(video.currentTime,13);
startPlayback('preview',0,'00:10','00:13',10);
video.currentTime=3.2;video.ontimeupdate();
assert.equal(autoPauses,2);
assert.equal(video.currentTime,3,'Preview playback must translate the source end to clip time');
startPlayback('source',10);$('loop').checked=true;
video.currentTime=13.2;video.ontimeupdate();
assert.equal(video.currentTime,10);
assert.equal(autoPauses,2,'Looping must restart instead of pausing');
startPlayback('source',10);video.paused=true;video.currentTime=20;video.ontimeupdate();
assert.equal(video.currentTime,20,'Paused manual seeking must not snap back to the marked end');
assert.equal(autoPauses,2);
for(const [mode,position,start,end] of [['final',10,'00:10','00:13'],['source',20,'00:10','00:13'],['source',0,'',''],['source',0,'00:13','00:10']]){
  startPlayback(mode,position,start,end);video.currentTime=30;video.ontimeupdate();
  assert.equal(autoPauses,2,'Final playback, manual browsing beyond the end, and invalid ranges stay unrestricted');
}
video.pause=originalPause;context.previewOffset=0;context.rangePlay=false;
console.log('Automatic pause: native play, preview offsets, loop, final video, and manual-location boundaries passed');

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

load('function updateAnalyzeCommand(', 'function updateExportCommand(');
context.data.analyze_command='python3 scripts/nba_highlights.py analyze --game-id 0022600102';
context.data.analyzed=false;
context.updateControls();
assert.equal($('copy-analyze').disabled,true);
assert.equal($('download').disabled,true);
context.data.selected_players=['201939'];
context.updateControls();
assert.equal($('copy-analyze').disabled,false);
assert($('analyze-command').textContent.endsWith(' --players 201939'));
assert.equal($('copy-export').disabled,true);
context.playerSelectionDirty=true;
context.updateControls();
assert.equal($('copy-analyze').disabled,true);
console.log('Analysis command: requires applied players, includes explicit scope, blocks premature export');

load('async function reloadLibrary(', '\nreloadLibrary();');
let libraryCalls=0,loaded=[];
context.api=async url=>{assert.equal(url,'/api/reviews');libraryCalls++;return [{id:'old',label:'old'},{id:'new',label:'待分析'}]};
context.option=()=>{};
context.loadBoard=async id=>loaded.push(id);
context.updateControls=()=>{};
$('board').replaceChildren=()=>{};
$('board').value='old';
(async()=>{
  await context.reloadLibrary();
  assert.equal(libraryCalls,1);
  assert.equal(loaded[0],'old');
  $('board').value='new';
  await context.reloadLibrary();
  assert.equal(libraryCalls,2,'Reload must refetch the game library');
  assert.equal(loaded[1],'new');
  console.log('Library refresh: fetches new games and preserves selection');
})().catch(error=>{console.error(error);process.exitCode=1});
