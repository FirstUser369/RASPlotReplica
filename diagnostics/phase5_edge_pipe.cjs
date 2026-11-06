// Local acceptance-test transport. Node is a development test dependency only.
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const edge = spawn('C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', [
  '--headless=new', '--disable-gpu', '--no-first-run', '--remote-debugging-pipe',
  `--user-data-dir=${process.argv[2]}`, 'about:blank',
], { windowsHide: true, stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });
let buffer = '', next = 0;
const pending = new Map();
edge.stdio[4].on('data', data => {
  buffer += data.toString();
  for (let end; (end = buffer.indexOf('\0')) !== -1;) {
    const msg = JSON.parse(buffer.slice(0, end)); buffer = buffer.slice(end + 1);
    const done = pending.get(msg.id);
    if (done) { pending.delete(msg.id); done(msg); }
  }
});
function call(method, params, sessionId) {
  const id = ++next;
  return new Promise(resolve => {
    pending.set(id, resolve);
    edge.stdio[3].write(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + '\0');
  });
}
(async () => {
  const target = await call('Target.createTarget', { url: 'about:blank' });
  const attached = await call('Target.attachToTarget', { targetId: target.result.targetId, flatten: true });
  const session = attached.result.sessionId;
  process.stdout.write('READY\n');
  for await (const line of readline.createInterface({ input: process.stdin })) {
    const request = JSON.parse(line);
    const result = await call(request.method, request.params || {}, request.method.startsWith('Browser.') ? undefined : session);
    process.stdout.write(JSON.stringify(result) + '\n');
  }
  edge.kill();
})().catch(error => { process.stderr.write(String(error)); edge.kill(); process.exit(1); });
