const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const trustedPath = '/usr/palm/services/com.palm.service.devmode';
const downloadDir = '/media/internal/downloads';

function runPage(file, search) {
  const html = fs.readFileSync(path.join(__dirname, '..', 'wwwroot', file), 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const timers = [];
  const calls = [];

  function PalmServiceBridge() {
    this.call = (uri, payload) => {
      calls.push({ uri, payload: JSON.parse(payload) });
      if (uri.endsWith('/cancelAllDownloads')) {
        this.onservicecallback('{"returnValue":true}');
      } else if (uri.endsWith('/download')) {
        this.onservicecallback('{"completed":true,"aborted":false,"interrupted":false}');
      }
    };
    this.cancel = () => {};
  }

  const context = {
    location: {
      href: `http://192.0.2.10:8080/${file}${search}`,
      protocol: 'http:', host: '192.0.2.10:8080', search,
    },
    document: { getElementById: () => ({ textContent: '' }) },
    PalmServiceBridge,
    Image: function Image() {},
    Date,
    setTimeout: fn => timers.push(fn),
  };
  vm.runInNewContext(script, context, { filename: file });

  if (file === 'index-webos26.html') {
    const run = timers.find(fn => fn.name === 'runService');
    assert.ok(run, 'primary service launch was not scheduled');
    run();
  } else {
    for (let i = 0; i < 30 && !calls.some(c => c.uri.endsWith('/run')); i++) {
      assert.ok(timers.length, 'compatible service launch was not scheduled');
      timers.shift()();
    }
  }

  const launch = calls.find(c => c.uri === 'luna://com.webos.service.jsserver/run');
  assert.ok(launch, 'jsserver/run was not called');
  return launch.payload.inputParams;
}

const primary = runPage(
  'index-webos26.html',
  '?script=autoroot-webos26.sh&files=package.json;main.js&fake-service-path=' + trustedPath,
);
assert.equal(primary,
  `a b c ${trustedPath} -- /tmp/run-js-service-no-cgroup ^_~ ` +
  `${downloadDir} ${downloadDir}/autoroot-webos26.sh`);

const original = runPage(
  'index-webos26.html',
  '?script=autoroot-webos26.sh&files=package.json;main.js',
);
assert.equal(original,
  `-- /tmp/run-js-service-no-cgroup ^_~ ${downloadDir} ` +
  `${downloadDir}/autoroot-webos26.sh`);

const compatible = runPage('run-webos26-compatible.html', '?attempt=1');
assert.equal(compatible,
  `a b c ${trustedPath} -- /tmp/cgroup dummy ${downloadDir}`);

console.log('payload launch arguments match the upstream trusted-path workaround');
