const form = document.querySelector('#job-form');
const customWordlist = document.querySelector('#custom-wordlist');
const submit = document.querySelector('#submit');
const cancelJobButton = document.querySelector('#cancel-job');
const progressCard = document.querySelector('#progress-card');
const resultCard = document.querySelector('#result-card');
const progressBar = document.querySelector('#progress-bar');
const statusBadge = document.querySelector('#status-badge');
const statusMessage = document.querySelector('#status-message');
const reportJson = document.querySelector('#report-json');
const health = document.querySelector('#health');
const toolCards = document.querySelector('#tool-cards');
const summaryGrid = document.querySelector('#summary-grid');
const artifactLink = document.querySelector('#artifact-link');
const reportLink = document.querySelector('#report-link');
const stageCards = [...document.querySelectorAll('.stage-card')];
const stageBars = Object.fromEntries([...document.querySelectorAll('[data-stage-bar]')].map(node => [node.dataset.stageBar, node]));
const stageValues = Object.fromEntries([...document.querySelectorAll('[data-stage-value]')].map(node => [node.dataset.stageValue, node]));
const timeline = document.querySelector('#timeline');

const stages = [
  { id: 'preparing', label: 'Preparing', start: 0, end: 15 },
  { id: 'recovering_access', label: 'Recovering access', start: 15, end: 65 },
  { id: 'decrypting', label: 'Decrypting', start: 65, end: 82 },
  { id: 'static_analysis', label: 'Static analysis', start: 82, end: 97 },
  { id: 'completed', label: 'Completed', start: 97, end: 100 }
];

const statusLabels = {
  queued: 'ממתין',
  running: 'בתהליך',
  completed: 'הושלם',
  failed: 'נכשל',
  cancelled: 'בוטל'
};

const verdictLabels = {
  no_obvious_findings: 'לא נמצאו אינדיקציות בולטות',
  review_recommended: 'מומלץ לבצע בדיקה ידנית'
};

const knownUiMessages = {
  Preparing: 'מכין את הניתוח',
  'Recovering access': 'מנסה לשחזר גישה לקובץ',
  Decrypting: 'מפענח את הקובץ',
  'Static analysis': 'מריץ ניתוח סטטי',
  Completed: 'הניתוח הושלם',
  Cancelled: 'הניתוח בוטל',
  'Unable to recover access within configured limits': 'לא ניתן היה לשחזר גישה במסגרת המגבלות שהוגדרו.',
  'Unable to recover access with configured policy': 'לא ניתן היה לשחזר גישה באמצעות המדיניות שהוגדרה.',
  Cancelling: 'מבטל את הניתוח…'
};

const knownUiErrors = {
  'Authorization confirmation is required': 'יש לאשר הרשאה לפני תחילת הניתוח.',
  'Uploaded file exceeds the configured limit': 'הקובץ שהועלה חורג מהמגבלה שהוגדרה.',
  'Job not found': 'העבודה לא נמצאה.',
  'Report is not ready': 'הדוח עדיין לא מוכן.',
  'Report not found': 'הדוח לא נמצא.',
  'Tool output not found': 'פלט הכלי לא נמצא.',
  'Artifact is not ready': 'ה־artifact עדיין לא מוכן.',
  'Cancel the analysis before deleting it': 'יש לבטל את הניתוח לפני המחיקה.',
  'Job creation failed': 'יצירת העבודה נכשלה.',
  'Cancellation failed': 'ביטול העבודה נכשל.',
  'Delete failed': 'מחיקת העבודה נכשלה.'
};

let currentJob = null;
let pollTimer = null;

const terminalStatuses = ['completed', 'failed', 'cancelled'];
const errorStatuses = ['failed', 'cancelled'];

function clearChildren(node) {
  node.replaceChildren();
}

function appendTextElement(parent, tagName, text, className = '') {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  node.textContent = text;
  parent.appendChild(node);
  return node;
}

function techify(node) {
  node.classList.add('tech-ltr');
  node.dir = 'ltr';
  return node;
}

function displayStatus(status) {
  return statusLabels[status] || status;
}

function displayVerdict(verdict) {
  return verdictLabels[verdict] || verdict || 'לא זמין';
}

function translateUiMessage(message) {
  return knownUiMessages[message] || message || '';
}

function translateUiError(message) {
  return knownUiErrors[message] || message || 'אירעה שגיאה.';
}

function currentStageId(stage, status) {
  if (stage && stages.some(item => item.id === stage)) return stage;
  if (terminalStatuses.includes(status)) return 'completed';
  return 'preparing';
}

function setTimeline(stage, status) {
  const current = currentStageId(stage, status);
  const currentIndex = stages.findIndex(item => item.id === current);
  clearChildren(timeline);
  stages.forEach((item, index) => {
    const span = document.createElement('span');
    span.textContent = item.label;
    if (currentIndex >= 0 && index <= currentIndex) span.classList.add('active');
    timeline.appendChild(span);
  });
}

function computeStagePercent(stageDef, overallProgress) {
  if (overallProgress <= stageDef.start) return 0;
  if (overallProgress >= stageDef.end) return 100;
  return Math.max(0, Math.min(100, ((overallProgress - stageDef.start) / (stageDef.end - stageDef.start)) * 100));
}

function updateProgress(progress) {
  const clamped = Math.max(0, Math.min(100, Number(progress || 0)));
  progressBar.style.width = `${clamped}%`;
}

function updateStageProgress(state) {
  const progress = Number(state.progress || 0);
  const current = currentStageId(state.stage, state.status);

  stageCards.forEach(card => {
    const stageId = card.dataset.stage;
    const stageDef = stages.find(item => item.id === stageId);
    const percent = Math.round(computeStagePercent(stageDef, progress));
    const bar = stageBars[stageId];
    const value = stageValues[stageId];
    if (bar) bar.style.width = `${percent}%`;
    if (value) value.textContent = `${percent}%`;
    card.classList.toggle('active', current === stageId && !terminalStatuses.includes(state.status));
    card.classList.toggle('done', percent >= 100 || (state.status === 'completed' && stageId === 'completed'));
  });
}

function applyStatusState(state) {
  statusBadge.textContent = displayStatus(state.status);
  statusBadge.className = errorStatuses.includes(state.status) ? 'badge status-badge error' : 'badge status-badge';
  statusMessage.textContent = translateUiMessage(state.message || '');
  statusMessage.className = errorStatuses.includes(state.status) ? 'status-message error' : 'status-message';
}

function renderSummaryGrid(summary) {
  clearChildren(summaryGrid);
  const summaryItems = [
    { label: 'הכרעה', value: displayVerdict(summary.verdict), technical: false },
    { label: 'קבצים', value: summary.file_count, technical: true },
    { label: 'סך הכול בתים', value: summary.total_bytes, technical: true },
    { label: 'אינדיקטורים', value: summary.indicator_count, technical: true },
    { label: 'פגיעות YARA', value: summary.yara_hits ? 'כן' : 'לא', technical: false },
    { label: 'אינדיקטורי מאקרו', value: summary.macro_indicators ? 'כן' : 'לא', technical: false }
  ];
  summaryItems.forEach(item => {
    const card = document.createElement('div');
    card.className = 'summary-card';
    appendTextElement(card, 'span', String(item.label), 'summary-label');
    const valueNode = appendTextElement(card, 'strong', String(item.value ?? 'לא זמין'), 'summary-value');
    if (item.technical) techify(valueNode);
    summaryGrid.appendChild(card);
  });
}

function buildPanel(label, content) {
  const wrapper = document.createElement('div');
  wrapper.className = 'tab-panel';
  wrapper.dataset.tabPanel = label;
  const pre = document.createElement('pre');
  pre.textContent = content;
  wrapper.appendChild(pre);
  return wrapper;
}

function buildToolCard(card, index) {
  const article = document.createElement('article');
  article.className = 'tool-card';
  article.dataset.toolCard = `tool-card-${index}`;

  const header = document.createElement('div');
  header.className = 'tool-card-header';

  const titleWrap = document.createElement('div');
  const title = document.createElement('h4');
  title.textContent = `${card.tool} · ${card.subject}`;
  techify(title);
  const meta = document.createElement('p');
  meta.textContent = `Version: ${card.tool_version || 'unavailable'} · Exit: ${card.exit_status ?? 'n/a'}`;
  techify(meta);
  titleWrap.append(title, meta);

  const rightWrap = document.createElement('div');
  rightWrap.className = 'tool-card-header-actions';
  const badge = document.createElement('span');
  badge.className = `badge ${card.available ? 'ok' : 'error'}`;
  badge.textContent = card.available ? 'available' : 'unavailable';
  techify(badge);
  rightWrap.appendChild(badge);
  if (card.raw_output_download) {
    const link = document.createElement('a');
    link.className = 'button secondary tool-download';
    link.href = `/api/jobs/${currentJob}/tool-output/${card.raw_output_download}`;
    link.textContent = 'הורדת פלט גולמי';
    rightWrap.appendChild(link);
  }

  header.append(titleWrap, rightWrap);

  const tabList = document.createElement('div');
  tabList.className = 'tab-list';
  tabList.setAttribute('role', 'tablist');
  tabList.setAttribute('aria-label', `לשוניות ${card.tool}`);

  const panels = [
    {
      key: 'native',
      label: card.tool === 'olevba' ? 'פלט מקורי' : 'פלט כלי',
      content: [
        card.raw_stdout || '',
        card.raw_stderr ? `--- stderr ---\n${card.raw_stderr}` : ''
      ].filter(Boolean).join(card.raw_stdout && card.raw_stderr ? '\n' : '') || 'לא נלכד פלט מהכלי.',
      truncated: Boolean(card.raw_stdout_truncated || card.raw_stderr_truncated),
      downloadTruncated: Boolean(card.raw_output_download_truncated)
    },
    {
      key: 'parsed',
      label: 'ממצאים מפוענחים',
      content: JSON.stringify(card.parsed_findings || {}, null, 2),
      truncated: false,
      downloadTruncated: false
    },
    {
      key: 'json',
      label: 'JSON',
      content: JSON.stringify(card, null, 2),
      truncated: false,
      downloadTruncated: false
    }
  ];

  const panelNodes = [];
  panels.forEach((panel, panelIndex) => {
    const button = document.createElement('button');
    button.className = `tab-button${panelIndex === 0 ? ' active' : ''}`;
    button.type = 'button';
    button.dataset.tabTarget = panel.key;
    button.textContent = panel.label;
    if (panel.label === 'JSON') techify(button);
    tabList.appendChild(button);

    const panelNode = buildPanel(panel.key, panel.content);
    if (panelIndex === 0) panelNode.classList.add('active');
    if (panel.truncated) {
      const note = document.createElement('p');
      note.className = 'tool-output-note';
      note.textContent = panel.downloadTruncated
        ? 'הפלט המוצג מקוצר. גם קובץ ה־raw-output להורדה מוגבל בגודל מטעמי בטיחות.'
        : 'הפלט המוצג מקוצר. אפשר להשתמש בהורדת raw-output כדי לקבל את הלכידה הבטוחה והגדולה יותר.';
      panelNode.appendChild(note);
    }
    panelNodes.push(panelNode);
  });

  article.append(header, tabList, ...panelNodes);

  const buttons = [...tabList.querySelectorAll('.tab-button')];
  buttons.forEach(button => {
    button.addEventListener('click', () => {
      buttons.forEach(candidate => candidate.classList.toggle('active', candidate === button));
      panelNodes.forEach(panel => panel.classList.toggle('active', panel.dataset.tabPanel === button.dataset.tabTarget));
    });
  });

  return article;
}

function renderToolCards(report) {
  clearChildren(toolCards);
  (report.tool_cards || []).forEach((card, index) => {
    toolCards.appendChild(buildToolCard(card, index));
  });
}

function setRunningControls(isRunning) {
  submit.disabled = isRunning;
  cancelJobButton.classList.toggle('hidden', !isRunning || !currentJob);
  cancelJobButton.disabled = false;
}

async function checkHealth() {
  try {
    const [healthResponse, capabilitiesResponse] = await Promise.all([
      fetch('/api/health'),
      fetch('/api/capabilities')
    ]);
    const healthData = await healthResponse.json();
    await capabilitiesResponse.json();
    health.textContent = healthData.ready ? 'מוכן' : 'לא מוכן';
    health.className = `health ${healthData.ready ? 'ok' : 'bad'}`;
  } catch {
    health.textContent = 'שגיאת חיבור';
    health.className = 'health bad';
  }
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  setRunningControls(true);
  resultCard.classList.add('hidden');
  clearChildren(toolCards);
  const body = new FormData(form);
  try {
    const response = await fetch('/api/jobs', { method: 'POST', body });
    const data = await response.json();
    if (!response.ok) throw new Error(translateUiError(data.detail || 'Job creation failed'));
    currentJob = data.job_id;
    progressCard.classList.remove('hidden');
    updateProgress(0);
    updateStageProgress({ progress: 0, stage: 'preparing', status: 'queued' });
    setTimeline('preparing', 'queued');
    await pollJob();
  } catch (error) {
    alert(translateUiError(error.message));
    setRunningControls(false);
  }
});

cancelJobButton.addEventListener('click', async () => {
  if (!currentJob) return;
  cancelJobButton.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${currentJob}/cancel`, { method: 'POST' });
    const payload = await response.json();
    if (!response.ok) throw new Error(translateUiError(payload.detail || 'Cancellation failed'));
    statusMessage.textContent = translateUiMessage(payload.message || 'Cancelling');
  } catch (error) {
    alert(translateUiError(error.message));
    cancelJobButton.disabled = false;
  }
});

async function pollJob() {
  clearTimeout(pollTimer);
  const response = await fetch(`/api/jobs/${currentJob}`);
  const state = await response.json();
  updateProgress(state.progress || 0);
  applyStatusState(state);
  updateStageProgress(state);
  setTimeline(state.stage, state.status);
  if (state.status === 'completed') {
    setRunningControls(false);
    await loadReport();
    return;
  }
  if (['failed', 'cancelled'].includes(state.status)) {
    setRunningControls(false);
    return;
  }
  pollTimer = setTimeout(pollJob, 1500);
}

async function loadReport() {
  const response = await fetch(`/api/jobs/${currentJob}/report`);
  const report = await response.json();
  renderSummaryGrid(report.summary || {});
  renderToolCards(report);
  reportJson.textContent = JSON.stringify(report, null, 2);
  artifactLink.href = `/api/jobs/${currentJob}/artifact`;
  reportLink.href = `/api/jobs/${currentJob}/report/download`;
  resultCard.classList.remove('hidden');
}

document.querySelector('#delete-job').addEventListener('click', async () => {
  if (!currentJob) return;
  const response = await fetch(`/api/jobs/${currentJob}`, { method: 'DELETE' });
  if (!response.ok) {
    const payload = await response.json();
    alert(translateUiError(payload.detail || 'Delete failed'));
    return;
  }
  currentJob = null;
  clearTimeout(pollTimer);
  resultCard.classList.add('hidden');
  progressCard.classList.add('hidden');
  clearChildren(toolCards);
  clearChildren(summaryGrid);
  form.reset();
  customWordlist.value = '';
  updateProgress(0);
  updateStageProgress({ progress: 0, stage: 'preparing', status: 'queued' });
  setTimeline('preparing', 'queued');
  setRunningControls(false);
});

window.__uiVersion = '20260713c';
window.__uiDebug = {
  updateProgress,
  updateStageProgress,
  setTimeline,
};

updateProgress(0);
updateStageProgress({ progress: 0, stage: 'preparing', status: 'queued' });
setTimeline('preparing', 'queued');
setRunningControls(false);
checkHealth();
