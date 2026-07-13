const form = document.querySelector('#job-form');
const custom = document.querySelector('#custom-wordlist');
const mounted = document.querySelector('#mounted-wordlist');
const submit = document.querySelector('#submit');
const progressCard = document.querySelector('#progress-card');
const resultCard = document.querySelector('#result-card');
const progressBar = document.querySelector('#progress-bar');
const statusBadge = document.querySelector('#status-badge');
const statusMessage = document.querySelector('#status-message');
const reportJson = document.querySelector('#report-json');
const health = document.querySelector('#health');
const revealButton = document.querySelector('#reveal-password');
const passwordPanel = document.querySelector('#password-panel');
const passwordField = document.querySelector('#revealed-password');
const passwordExpiry = document.querySelector('#password-expiry');
const flightTrail = document.querySelector('#flight-trail');
const flightPlane = document.querySelector('#flight-plane');
const stageCards = [...document.querySelectorAll('.stage-card')];
const stageBars = Object.fromEntries([...document.querySelectorAll('[data-stage-bar]')].map(node => [node.dataset.stageBar, node]));
const stageValues = Object.fromEntries([...document.querySelectorAll('[data-stage-value]')].map(node => [node.dataset.stageValue, node]));

const stages = [
  { id: 'extract_hash', label: 'חילוץ hash', start: 0, end: 10 },
  { id: 'crack', label: 'JtR', start: 10, end: 50 },
  { id: 'recover_secret', label: 'סוד זמני', start: 50, end: 65 },
  { id: 'decrypt', label: 'פענוח', start: 65, end: 80 },
  { id: 'static_scan', label: 'סריקה סטטית', start: 80, end: 100 }
];

let currentJob = null;
let pollTimer = null;
let passwordTimer = null;

const terminalStatuses = ['completed', 'failed', 'cancelled', 'not_cracked', 'timed_out'];
const errorStatuses = ['failed', 'cancelled', 'not_cracked', 'timed_out'];

function currentStageId(stage, status) {
  if (stage && stages.some(item => item.id === stage)) return stage;
  if (status === 'completed') return 'static_scan';
  return null;
}

function setTimeline(stage, status) {
  const current = currentStageId(stage, status);
  const currentIndex = stages.findIndex(item => item.id === current);
  document.querySelector('#timeline').innerHTML = stages.map((item, index) =>
    `<span class="${currentIndex >= 0 && index <= currentIndex ? 'active' : ''}">${item.label}</span>`).join('');
}

function computeStagePercent(stageDef, overallProgress) {
  if (overallProgress <= stageDef.start) return 0;
  if (overallProgress >= stageDef.end) return 100;
  return Math.max(0, Math.min(100, ((overallProgress - stageDef.start) / (stageDef.end - stageDef.start)) * 100));
}

function updateFlight(progress) {
  const clamped = Math.max(0, Math.min(100, progress || 0));
  progressBar.style.width = `${clamped}%`;
  flightTrail.style.width = `${clamped}%`;
  flightPlane.style.insetInlineStart = `${clamped}%`;
}

function updateStageProgress(state) {
  const progress = Number(state.progress || 0);
  const current = currentStageId(state.stage, state.status);

  stageCards.forEach(card => {
    const stageId = card.dataset.stage;
    const percent = Math.round(computeStagePercent(stages.find(item => item.id === stageId), progress));
    const bar = stageBars[stageId];
    const value = stageValues[stageId];
    if (bar) bar.style.width = `${percent}%`;
    if (value) value.textContent = `${percent}%`;
    card.classList.toggle('active', current === stageId && !terminalStatuses.includes(state.status));
    card.classList.toggle('done', percent >= 100 || (state.status === 'completed' && stageId === 'static_scan'));
  });
}

function formatTerminalMessage(state) {
  const base = (state.message || '').trim();
  if (state.status === 'not_cracked' || state.status === 'timed_out') {
    return `${base} לא בוצעה סריקה סטטית, כי לא נוצר עותק מפוענח בלי סיסמה.`.trim();
  }
  if (state.status === 'failed') {
    return `${base} לא בוצעה סריקה סטטית, כי תהליך הפיצוח או הפענוח נכשל לפני שנוצר עותק מפוענח.`.trim();
  }
  if (state.status === 'cancelled') {
    return `${base} לא בוצעה סריקה סטטית, כי העבודה בוטלה לפני שנוצר עותק מפוענח.`.trim();
  }
  return base;
}

function applyStatusState(state) {
  statusBadge.textContent = state.status;
  statusBadge.className = errorStatuses.includes(state.status) ? 'badge status-badge error' : 'badge status-badge';
  statusMessage.textContent = terminalStatuses.includes(state.status) ? formatTerminalMessage(state) : (state.message || '');
  statusMessage.className = errorStatuses.includes(state.status) ? 'status-message error' : 'status-message';
}

document.querySelectorAll('input[name="wordlist_mode"]').forEach(radio => {
  radio.addEventListener('change', () => {
    const customSelected = radio.value === 'custom' && radio.checked;
    const mountedSelected = radio.value === 'mounted' && radio.checked;
    custom.disabled = !customSelected;
    custom.required = customSelected;
    mounted.disabled = !mountedSelected;
    mounted.required = mountedSelected;
  });
});

async function checkHealth() {
  try {
    const [healthResponse, capabilitiesResponse] = await Promise.all([
      fetch('/api/health'),
      fetch('/api/capabilities')
    ]);
    const data = await healthResponse.json();
    const capabilities = await capabilitiesResponse.json();
    const mountedWordlists = capabilities.wordlists?.mounted || [];
    mounted.innerHTML = ['<option value="">בחר wordlist מותקן</option>']
      .concat(mountedWordlists.map(name => `<option value="${name}">${name}</option>`))
      .join('');
    health.textContent = data.ready ? `המערכת מוכנה (${data.runner_backend})` : 'המערכת לא מוכנה';
    health.className = `health ${data.ready ? 'ok' : 'bad'}`;
  } catch {
    health.textContent = 'שגיאת חיבור';
    health.className = 'health bad';
  }
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  submit.disabled = true;
  resultCard.classList.add('hidden');
  const body = new FormData(form);
  try {
    const response = await fetch('/api/jobs', { method: 'POST', body });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'יצירת העבודה נכשלה');
    currentJob = data.job_id;
    progressCard.classList.remove('hidden');
    updateFlight(0);
    updateStageProgress({ progress: 0, stage: 'extract_hash', status: 'queued' });
    setTimeline('extract_hash', 'queued');
    await pollJob();
  } catch (error) {
    alert(error.message);
    submit.disabled = false;
  }
});

async function pollJob() {
  clearTimeout(pollTimer);
  const response = await fetch(`/api/jobs/${currentJob}`);
  const state = await response.json();
  updateFlight(state.progress || 0);
  applyStatusState(state);
  updateStageProgress(state);
  setTimeline(state.stage, state.status);
  if (state.status === 'completed') {
    submit.disabled = false;
    await loadReport(state);
    return;
  }
  if (['failed', 'not_cracked', 'cancelled', 'timed_out'].includes(state.status)) {
    submit.disabled = false;
    return;
  }
  pollTimer = setTimeout(pollJob, 1500);
}

async function loadReport(state) {
  const response = await fetch(`/api/jobs/${currentJob}/report`);
  const report = await response.json();
  const s = report.summary;
  const summaryItems = [
    { label: 'Verdict', value: s.verdict, cardClass: 'summary-card verdict' },
    { label: 'Risk score', value: s.score, cardClass: 'summary-card' },
    { label: 'Files', value: s.file_count, cardClass: 'summary-card' },
    { label: 'ClamAV', value: s.clamav_hits ? 'Hit' : 'No hit', cardClass: 'summary-card' },
    { label: 'YARA', value: s.yara_hits ? 'Hit' : 'No hit', cardClass: 'summary-card' },
    { label: 'Macro indicators', value: s.macro_indicators ? 'Found' : 'None', cardClass: 'summary-card' }
  ];
  document.querySelector('#summary-grid').innerHTML = summaryItems.map(item => `
    <div class="${item.cardClass}">
      <span class="summary-label">${item.label}</span>
      <strong class="summary-value">${item.value}</strong>
    </div>
  `).join('');
  reportJson.textContent = JSON.stringify(report, null, 2);
  document.querySelector('#artifact-link').href = `/api/jobs/${currentJob}/artifact`;
  document.querySelector('#report-link').href = `/api/jobs/${currentJob}/report/download`;
  revealButton.disabled = !state.password_available;
  revealButton.textContent = state.password_available ? 'Reveal סיסמה' : 'סיסמה לא זמינה';
  clearRevealedPassword();
  resultCard.classList.remove('hidden');
}

function clearRevealedPassword() {
  clearTimeout(passwordTimer);
  passwordTimer = null;
  passwordField.value = '';
  passwordField.type = 'password';
  passwordPanel.classList.add('hidden');
  if (!revealButton.disabled) revealButton.textContent = 'Reveal סיסמה';
}

revealButton.addEventListener('click', async () => {
  if (!currentJob || revealButton.disabled) return;
  if (!passwordPanel.classList.contains('hidden')) {
    clearRevealedPassword();
    return;
  }

  revealButton.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${currentJob}/reveal-password`, {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Accept': 'application/json' }
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'חשיפת הסיסמה נכשלה');

    passwordField.value = data.password;
    passwordField.type = 'text';
    passwordPanel.classList.remove('hidden');
    revealButton.textContent = 'הסתר סיסמה';

    let remaining = Number(data.display_seconds || 30);
    passwordExpiry.textContent = `תימחק מהמסך בעוד ${remaining} שניות`;
    const tick = () => {
      remaining -= 1;
      if (remaining <= 0) {
        clearRevealedPassword();
        return;
      }
      passwordExpiry.textContent = `תימחק מהמסך בעוד ${remaining} שניות`;
      passwordTimer = setTimeout(tick, 1000);
    };
    passwordTimer = setTimeout(tick, 1000);
  } catch (error) {
    alert(error.message);
  } finally {
    revealButton.disabled = false;
  }
});

document.querySelector('#delete-job').addEventListener('click', async () => {
  if (!currentJob) return;
  await fetch(`/api/jobs/${currentJob}`, { method: 'DELETE' });
  currentJob = null;
  resultCard.classList.add('hidden');
  progressCard.classList.add('hidden');
  form.reset();
  custom.disabled = true;
  custom.required = false;
  mounted.disabled = true;
  mounted.required = false;
  clearRevealedPassword();
  updateFlight(0);
  updateStageProgress({ progress: 0, stage: 'extract_hash', status: 'queued' });
  setTimeline(null, 'queued');
});

window.__uiVersion = '20260712p';
window.__uiDebug = {
  updateFlight,
  updateStageProgress,
  setTimeline,
};

updateFlight(0);
updateStageProgress({ progress: 0, stage: 'extract_hash', status: 'queued' });
setTimeline(null, 'queued');
checkHealth();
