const { spawn } = require('node:child_process');
const path = require('node:path');

const frontendRoot = path.resolve(__dirname, '..');
const repositoryRoot = path.resolve(frontendRoot, '..');
const sourceRoot = path.resolve(repositoryRoot, 'backend', 'src');
const fixtureServer = path.resolve(repositoryRoot, 'tests', 'support', 'serve_fixture.py');
const installed = process.env.AUTONOMO_BROWSER_INSTALLED === '1';
const python = process.env.PYTHON || 'python';
const environment = { ...process.env };

if (installed) delete environment.PYTHONPATH;
else environment.PYTHONPATH = sourceRoot;

const child = spawn(python, installed ? ['-I', fixtureServer] : [fixtureServer], {
  cwd: repositoryRoot,
  env: environment,
  shell: false,
  stdio: 'inherit',
});

const signals = ['SIGINT', 'SIGTERM', 'SIGHUP'];
let forwardedSignal;
let launchError = false;

function forwardSignal(signal) {
  forwardedSignal ??= signal;
  child.kill(signal);
}

for (const signal of signals) {
  process.on(signal, () => forwardSignal(signal));
}

child.on('error', (error) => {
  launchError = true;
  console.error(`Could not start browser fixture server: ${error.message}`);
});

child.on('close', (code, signal) => {
  if (forwardedSignal || signal) {
    const exitSignal = forwardedSignal ?? signal;
    for (const signalName of signals) process.removeAllListeners(signalName);
    process.kill(process.pid, exitSignal);
  } else if (launchError) process.exit(1);
  else process.exit(code ?? 1);
});
