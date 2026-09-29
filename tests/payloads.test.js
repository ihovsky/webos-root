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
  const beacons = [];

  function PalmServiceBridge() {
    this.call = (uri, payload) => {
      calls.push({ uri, payload: JSON.parse(payload) });
      if (uri.endsWith('/cancelAllDownloads')) {
        this.onservicecallback('{"returnValue":true}');
      } else if (uri.endsWith('/download')) {
        this.onservicecallback('{"completed":true,"aborted":false,"interrupted":false}');
      } else if (uri.endsWith('/run')) {
        this.onservicecallback('{"returnValue":true}');
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
    Image: function Image() {
      Object.defineProperty(this, 'src', { set: url => beacons.push(url) });
    },
    Date,
    setTimeout: fn => timers.push(fn),
  };
  vm.runInNewContext(script, context, { filename: file });

  if (file === 'index-webos26.html') {
    timers[0]();
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
  return {
    inputParams: launch.payload.inputParams,
    downloadTargets: calls.filter(c => c.uri.endsWith('/download')).map(c => c.payload.target),
    beacons,
  };
}

const primary = runPage(
  'index-webos26.html',
  '?script=autoroot-webos26.sh&files=package.json;main.js&fake-service-path=' + trustedPath,
);
assert.equal(primary.inputParams,
  `a b c ${trustedPath} -- /tmp/run-js-service-no-cgroup ^_~ ` +
  `${downloadDir} ${downloadDir}/autoroot-webos26.sh`);
assert.deepEqual(primary.downloadTargets, ['http://192.0.2.10:8080/package.json']);
assert.ok(primary.beacons.some(url => url.startsWith('http://192.0.2.10:8080/primary-run-result?')));

const original = runPage(
  'index-webos26.html',
  '?script=autoroot-webos26.sh&files=package.json;main.js',
);
assert.equal(original.inputParams,
  `-- /tmp/run-js-service-no-cgroup ^_~ ${downloadDir} ` +
  `${downloadDir}/autoroot-webos26.sh`);

const compatible = runPage('run-webos26-compatible.html', '?attempt=1');
assert.equal(compatible.inputParams,
  `a b c ${trustedPath} -- /tmp/cgroup dummy ${downloadDir}`);

console.log('payload launch arguments match the upstream trusted-path workaround');
