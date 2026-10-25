// Optional local integration test: Node 22+, installed Chromium, project Python venv.
// Uses an isolated headless profile and CDP. It never opens a real learning platform.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn, execFileSync } from 'node:child_process';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.resolve(process.argv[2] || path.join(root, 'tmp/mock-browser-acceptance', String(Date.now())));
const browserPath = process.env.CAMPUS_HOMEWORK_BROWSER || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/chromium', '/usr/bin/google-chrome',
].find(existsSync);
const python = process.env.CAMPUS_HOMEWORK_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
if (!browserPath || !existsSync(python)) throw new Error('Set CAMPUS_HOMEWORK_BROWSER and CAMPUS_HOMEWORK_PYTHON to installed executables.');
await fs.mkdir(output, { recursive: true });
if (existsSync(path.join(output, 'run.json'))) throw new Error('Choose a fresh evidence directory; an existing run must not be overwritten.');
const profile = await fs.mkdtemp(path.join(output, 'isolated-profile-'));
const record = { mock_only: true, headed: false, existing_user_profile_used: false, steps: [], dialogs: [], network_requests: [] };
let browser, socket, sequence = 0;
const pending = new Map();
let dialogDecision = null;
function pythonRun(code, input = {}) {
  return JSON.parse(execFileSync(python, ['-c', `import sys,json;sys.path.insert(0,${JSON.stringify(path.join(root, 'scripts'))});${code}`],
    { input: JSON.stringify(input), encoding: 'utf8', windowsHide: true, timeout: 30000,
      env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' } }).trim());
}
function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 15000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const answer = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (answer.exceptionDetails) throw new Error(JSON.stringify(answer.exceptionDetails));
  return answer.result.value;
}
async function readback() {
  return evaluate(`(async()=>({title:document.title,url:location.href,
    options:Object.fromEntries(['q1','q2'].map(name=>[name,Array.from(document.querySelectorAll('input[name='+name+']')).map(e=>({label:e.value,text:e.closest('label').textContent.trim().replace(/^[A-Z]\\.\\s*/,''),selected:e.checked}))])),
    fill:document.querySelector('[name=q3]').value,answer:document.querySelector('#answer').value,
    attachment:await(async()=>{const f=document.querySelector('#attachment').files[0];return f?{name:f.name,type:f.type,size:f.size,sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await f.arrayBuffer()))).map(x=>x.toString(16).padStart(2,'0')).join('')}:null})(),
    attachment_status:document.querySelector('#attachment-status').textContent,
    status:document.querySelector('#status').textContent,
    judgment_controls:document.querySelectorAll('[name=judgment]').length,
    file_multiple:document.querySelector('#attachment').multiple,
    accept:document.querySelector('#attachment').accept}))()`);
}
async function snapshot(name) {
  const dom = await readback();
  await fs.writeFile(path.join(output, `${name}.json`), JSON.stringify(dom, null, 2));
  const { cssContentSize } = await call('Page.getLayoutMetrics');
  const screenshot = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: Math.ceil(cssContentSize.width), height: Math.ceil(cssContentSize.height), scale: 1 } });
  const evidence = path.join(output, `${name}.png`);
  await fs.writeFile(evidence, Buffer.from(screenshot.data, 'base64'));
  record.steps.push({ name, dom, screenshot: evidence });
  return { dom, evidence };
}

try {
  browser = spawn(browserPath, ['--headless=new', '--no-first-run', '--no-default-browser-check',
    '--disable-extensions', '--disable-background-networking', '--disable-component-update',
    '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
    { stdio: 'ignore', windowsHide: true });
  const started = Date.now();
  let port;
  while (!port) {
    try { port = Number((await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]); } catch {}
    if (Date.now() - started > 15000) throw new Error('Isolated Chrome did not start within 15 seconds.');
    if (!port) await new Promise(resolve => setTimeout(resolve, 100));
  }
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = targets.find(item => item.type === 'page');
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const request = pending.get(message.id); pending.delete(message.id); clearTimeout(request.timer);
      message.error ? request.reject(new Error(JSON.stringify(message.error))) : request.resolve(message.result);
    } else if (message.method === 'Page.javascriptDialogOpening') {
      record.dialogs.push(message.params);
      if (message.params.type !== 'confirm' || !message.params.message.includes('本地模拟')) {
        call('Page.handleJavaScriptDialog', { accept: false }).catch(() => {});
      } else call('Page.handleJavaScriptDialog', { accept: dialogDecision === 'accept' }).catch(() => {});
    } else if (message.method === 'Network.requestWillBeSent') record.network_requests.push(message.params.request.url);
  };
  await call('Page.enable'); await call('Runtime.enable'); await call('Network.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1200, height: 1000, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: pathToFileURL(path.join(root, 'examples/mock-platform.html')).href });
  while (!(await evaluate('Boolean(document.querySelector("#homework"))'))) await new Promise(resolve => setTimeout(resolve, 50));
  const initial = await snapshot('01-initial');
  assert.equal(initial.dom.status, '状态：未保存');

  const prepared = pythonRun(`
from pathlib import Path
from campus_homework.model import match_options,require_valid
from campus_homework.rendering import render_document
from campus_homework.state import RunStore
v=json.load(sys.stdin);out=Path(v['output']);opts=v['options']
review={'status':'verified','reviewer':'local-mock-independent-check','method':'Differentiate s, calculate tangential/normal acceleration and exact SHM identities','checks':dict.fromkeys(['conditions','answer','symbols','units','language'],True)}
def q(qid,n,kind,stem,final,**extra):
 return {'id':qid,'number':n,'type':kind,'stem':stem,'source':{'kind':'synthetic-local-mock','complete':True},'solution':{'steps':[{'text':'独立推导并核对符号和单位。'}],'final':final},'review':review,**extra}
text='''v=ds/dt=1.5t²，t=1 s时v=1.5 m/s。
aτ=dv/dt=3t=3 m/s²，沿切向向上；an=v²/R=4.5 m/s²，沿法向指向圆心（向左）。
a=√(3²+4.5²)=1.5√13≈5.408 m/s²。'''
q1=q('q1',1,'single','s=0.5t³，R=0.5 m，求t=1 s时合加速度',{'option_ids':['correct']},options=[{'id':str(i),'label':'old-'+str(i),'text':o['text']} for i,o in enumerate(opts['q1'])])
q1['options'][next(i for i,o in enumerate(q1['options']) if o['text']=='5.408 m/s²')]['id']='correct'
q2=q('q2',2,'multiple','理想简谐振动的正确陈述',{'option_ids':['acceleration','speed']},options=[{'id':'acceleration','text':'a = −ω²x'},{'id':'speed','text':'平衡位置速率最大'},{'id':'zero','text':'加速度始终为零'}])
task={'schema_version':1,'assignment':{'id':'local-browser-mock','title':'本地模拟验收','course':'虚构基础物理','kind':'homework'},'questions':[q1,q2,q('q3',3,'fill','角频率ω对应周期',{'values':['2π/ω']},blank_count=1),q('q4',4,'calculation','圆周运动计算过程及附图',{'text':text},delivery='image')]}
require_valid(task,True)
image_task=json.loads(json.dumps(task));image_task['questions']=[image_task['questions'][3]]
image_task['questions'][0]['solution']={'steps':[{'text':text}],'final':{'tex':r'a=\\sqrt{3^2+4.5^2}=\\frac{3\\sqrt{13}}2\\,\\mathrm{m/s^2}'}}
rendered=render_document(image_task,out/'attachment',{'font':'chenyu','handwriting':'natural','ink':'#202126','ink_effect':'ballpoint'})
run=RunStore.create(out/'run.json',task);run.register_artifacts('q4',[rendered['upload_pages'][0]])
try:run.submission_gate();raise AssertionError('unfilled gate incorrectly passed')
except ValueError:pass
out.joinpath('reviewed-task.json').write_text(json.dumps(task,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'q1_labels':match_options(q1,opts['q1']),'q2_labels':match_options(q2,opts['q2']),'fill':'2π/ω','text':text,'attachment':rendered['upload_pages'][0]}))
`, { output, options: initial.dom.options });
  record.option_mapping = { q1: prepared.q1_labels, q2: prepared.q2_labels, old_labels_not_used: true };
  await evaluate(`(()=>{const data=${JSON.stringify(prepared)};for(const name of ['q1','q2'])for(const label of data[name+'_labels'])document.querySelector('input[name='+name+'][value='+label+']').click();
    for(const [selector,value] of [['[name=q3]',data.fill],['#answer',data.text]]){const e=document.querySelector(selector);e.focus();e.value=value;e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}})()`);
  const document = await call('DOM.getDocument');
  const fileInput = await call('DOM.querySelector', { nodeId: document.root.nodeId, selector: '#attachment' });
  await call('DOM.setFileInputFiles', { nodeId: fileInput.nodeId, files: [prepared.attachment] });
  const filled = await snapshot('02-filled-attachment');
  assert.deepEqual(filled.dom.options.q1.filter(o => o.selected).map(o => o.label), prepared.q1_labels);
  assert.deepEqual(filled.dom.options.q2.filter(o => o.selected).map(o => o.label), prepared.q2_labels);
  assert.equal(filled.dom.fill, prepared.fill); assert.equal(filled.dom.answer, prepared.text);
  assert.equal(filled.dom.attachment.type, 'image/jpeg'); assert.ok(filled.dom.attachment.size > 0);
  pythonRun(`
from pathlib import Path
from campus_homework.state import RunStore
v=json.load(sys.stdin);s=RunStore(Path(v['output'])/'run.json');t=s.read()['task'];dom=v['dom']
for q in t['questions']:
 actual=q['solution']['final'].copy()
 if q['id']=='q4':actual={'text':dom['answer'],'uploaded_sha256':[dom['attachment']['sha256']]}
 elif q['id']=='q3':actual={'values':[dom['fill']]}
 s.record_filled(q['id'],actual,v['evidence'])
print(json.dumps({'readbacks_recorded':4}))
`, { output, dom: filled.dom, evidence: filled.evidence });
  await evaluate('document.querySelector("#save").click()');
  const saved = await snapshot('03-saved'); assert.ok(saved.dom.status.includes('保存成功'));
  const gate = pythonRun(`from pathlib import Path
from campus_homework.state import RunStore
v=json.load(sys.stdin);s=RunStore(Path(v['output'])/'run.json');s.save_verified(v['evidence'],v['status']);print(json.dumps(s.submission_gate()))`,
    { output, evidence: saved.evidence, status: saved.dom.status });
  assert.equal(gate.ready, true); record.submission_gate = gate;
  dialogDecision = 'cancel'; await evaluate('document.querySelector("button[type=submit]").click()');
  const cancelled = await snapshot('04-submit-cancelled'); assert.equal(cancelled.dom.status, saved.dom.status);
  pythonRun(`from pathlib import Path
from campus_homework.state import RunStore
v=json.load(sys.stdin);s=RunStore(Path(v['output'])/'run.json');s.begin_submission();print(json.dumps({'status':s.read()['status']}))`, { output });
  dialogDecision = 'accept'; await evaluate('document.querySelector("button[type=submit]").click()');
  const submitted = await snapshot('05-submitted');
  assert.ok(submitted.dom.status.startsWith('状态：提交成功（本地模拟）· '));
  const submittedAt = submitted.dom.status.split('· ')[1]; assert.ok(Number.isFinite(Date.parse(submittedAt)));
  const journal = pythonRun(`from pathlib import Path
from campus_homework.state import RunStore
v=json.load(sys.stdin);s=RunStore(Path(v['output'])/'run.json');s.confirm_submission(v['evidence'],v['status'],v['time']);r=s.read();print(json.dumps({'status':r['status'],'score':r['submission']['score']}))`,
    { output, evidence: submitted.evidence, status: submitted.dom.status, time: submittedAt });
  assert.equal(journal.status, 'submitted'); assert.equal(journal.score, null);
  await call('Page.reload');
  const reloadDeadline = Date.now() + 5000;
  while (!(await evaluate('document.querySelector("#status")?.textContent === "状态：未保存"'))) {
    if (Date.now() > reloadDeadline) throw new Error('Reload did not reach the initial mock state.');
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  const reload = await snapshot('06-reload-scope-check');
  assert.equal(reload.dom.status, '状态：未保存'); assert.equal(reload.dom.answer, '');
  assert.equal(reload.dom.attachment, null);
  assert.equal(record.network_requests.filter(url => /^https?:/.test(url)).length, 0);
  record.passed = true;
  record.limitations = ['Local mock only; no real Chaoxing execution or score.', 'No judgment controls.', 'One selected local attachment; no server upload/preview.', 'Save/submit change current page status only; reload clears data.', 'No answer completeness validation, grading, or post-submit locking.'];
  console.log(JSON.stringify({ passed: true, mock_only: true, steps: record.steps.length, output, option_mapping: record.option_mapping, journal, limitations: record.limitations }, null, 2));
} catch (error) {
  record.passed = false; record.error = String(error.stack || error); console.error(record.error); process.exitCode = 1;
} finally {
  await fs.writeFile(path.join(output, 'acceptance.json'), JSON.stringify(record, null, 2));
  if (socket?.readyState === WebSocket.OPEN) {
    try { await call('Browser.close'); } catch {}
    socket.close();
  }
  if (browser && browser.exitCode === null) {
    await Promise.race([new Promise(resolve => browser.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 3000))]);
    if (browser.exitCode === null) browser.kill();
  }
  // Verify this is the task-created private profile before recursive removal on Windows.
  if (path.resolve(profile).startsWith(output + path.sep) && path.basename(profile).startsWith('isolated-profile-')) {
    try { await fs.rm(profile, { recursive: true, force: true, maxRetries: 4, retryDelay: 250 }); }
    catch (error) { await fs.writeFile(path.join(output, 'profile-cleanup-note.txt'), String(error)); }
  }
}
