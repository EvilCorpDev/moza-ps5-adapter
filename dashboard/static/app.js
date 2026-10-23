'use strict';
const $ = (id) => document.getElementById(id);
let token = '', connected = false, busy = false, polling = false, lastId = 0, state = 'stopped';
let statusError = '', externalWarning = false;
const labels = { stopped: 'OFF', starting: 'STARTING', running: 'RUNNING', stopping: 'STOPPING', error: 'ERROR', external: 'EXTERNAL' };

function notice(text, error = false) {
  $('notice').textContent = text;
  $('notice').className = 'notice' + (error ? ' error' : '') + (text ? '' : ' hidden');
}
function paint(id, text, tone = '') {
  $(id).textContent = text;
  $(id).className = tone;
}
function buttons() {
  $('start').disabled = !connected || busy || !['stopped', 'error'].includes(state);
  $('stop').disabled = !connected || busy || !['starting', 'running'].includes(state);
}
function duration(seconds) {
  const minutes = Math.floor(seconds / 60), remainder = seconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
}
function timeLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString([], {hour12: false});
}
function render(data) {
  token = data.token;
  connected = true;
  state = data.state;
  $('server-dot').className = 'dot online';
  $('server-status').textContent = 'DASHBOARD ONLINE';
  $('clock').textContent = timeLabel(data.server_time);
  const live = ['starting', 'running', 'stopping'].includes(state);
  const tone = state === 'error' ? 'bad' : state === 'running' ? 'good' : live ? 'warn' : '';
  paint('bridge-state', labels[state] || state.toUpperCase(), tone);
  $('bridge-detail').textContent = state === 'external' ? 'Started outside dashboard' : live ? `Session ${duration(data.uptime)}` : 'Ready when you are';
  $('state-tag').textContent = labels[state] || state.toUpperCase();
  $('state-tag').className = 'state-tag' + (state === 'running' ? ' live' : state === 'error' ? ' error' : '');
  const devices = data.devices;
  paint('r5-state', devices.r5_live === true ? 'LIVE' : devices.r5_detected ? 'DETECTED' : 'NOT FOUND', devices.r5_live === true || devices.r5_detected ? 'good' : 'warn');
  paint('pico-state', devices.pico_mounted === true ? 'CONNECTED' : devices.pico_mounted === false ? 'UNPLUGGED' : 'WAITING', devices.pico_mounted === true ? 'good' : '');
  $('pico-detail').textContent = live ? 'Pico USB connection' : 'Checks when bridge starts';
  $('uptime').textContent = live ? `SESSION ${duration(data.uptime)}` : 'SESSION —';
  $('console-status').textContent = state === 'external' ? 'bridge started outside dashboard' : state === 'running' ? (devices.outputs_ready ? 'bridge online · game output live' : 'bridge online · waiting for PS5') : state === 'stopped' ? 'waiting for start' : state;
  const rows = data.logs || [];
  if (rows.length && rows[rows.length - 1].id < lastId) {
    $('logs').replaceChildren(); lastId = 0;
  }
  const fresh = rows.filter((row) => row.id > lastId);
  if (fresh.length) {
    $('empty-log')?.remove();
    for (const row of fresh) {
      const line = document.createElement('div');
      line.className = 'log-line ' + (['system', 'error'].includes(row.kind) ? row.kind : 'info');
      const time = document.createElement('span'); time.className = 'log-time'; time.textContent = timeLabel(row.time);
      const text = document.createElement('span'); text.className = 'log-text'; text.textContent = row.text;
      line.append(time, text); $('logs').append(line); lastId = row.id;
    }
    while ($('logs').childElementCount > 180) $('logs').firstElementChild.remove();
    if ($('autoscroll').checked) $('logs').scrollTop = $('logs').scrollHeight;
  }
  const count = $('logs').querySelectorAll('.log-line').length;
  $('line-count').textContent = `${count} ${count === 1 ? 'LINE' : 'LINES'}`;
  const nextError = data.error || '';
  if (nextError !== statusError) {
    statusError = nextError;
    notice(statusError, Boolean(statusError));
  }
  if (state === 'external' && !statusError) {
    notice('Bridge is running in another terminal. Stop it there before starting a dashboard session.');
    externalWarning = true;
  } else if (externalWarning) {
    externalWarning = false;
    if (!statusError) notice('');
  }
  buttons();
}
async function refresh() {
  if (polling) return;
  polling = true;
  try {
    const response = await fetch('/api/status', {cache: 'no-store', signal: AbortSignal.timeout(4500)});
    if (!response.ok) throw new Error('Dashboard unavailable');
    const data = await response.json();
    const wasOffline = !connected;
    if (wasOffline) { notice(''); statusError = ''; }
    render(data);
  } catch (_) {
    connected = false;
    $('server-dot').className = 'dot';
    $('server-status').textContent = 'CONNECTION LOST';
    notice('Cannot reach wheelpi. Check the Pi and network; this page will reconnect automatically.', true);
    buttons();
  } finally { polling = false; }
}
async function action(name) {
  if (!connected || busy || $(name).disabled) return;
  busy = true; buttons(); notice('');
  try {
    const response = await fetch(`/api/${name}`, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Bridge-Token': token}, body: '{}', signal: AbortSignal.timeout(5000)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The request failed.');
    await refresh();
  } catch (error) { notice(error.message || 'Request failed.', true); }
  finally { busy = false; buttons(); }
}
$('start').addEventListener('click', () => action('start'));
$('stop').addEventListener('click', () => action('stop'));
document.addEventListener('keydown', (event) => {
  if (event.repeat || event.ctrlKey || event.altKey || event.metaKey || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName) || event.target.isContentEditable) return;
  if (event.key.toLowerCase() === 's') { event.preventDefault(); action('start'); }
  if (event.key.toLowerCase() === 'x') { event.preventDefault(); action('stop'); }
});
(async function poll() { await refresh(); setTimeout(poll, 1000); })();
