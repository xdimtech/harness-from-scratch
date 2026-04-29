const state = {
  data: null,
  selectedRunId: null,
  selectedFilePath: null,
  selectedArtifactLabel: null,
};

const els = {
  form: document.getElementById('mission-form'),
  input: document.getElementById('mission-input'),
  startBtn: document.getElementById('start-btn'),
  formStatus: document.getElementById('form-status'),
  overview: document.getElementById('overview'),
  runSelect: document.getElementById('run-select'),
  taskColumns: document.getElementById('task-columns'),
  timeline: document.getElementById('timeline'),
  agents: document.getElementById('agents'),
  worktrees: document.getElementById('worktrees'),
  jobs: document.getElementById('jobs'),
  artifactList: document.getElementById('artifact-list'),
  fileContent: document.getElementById('file-content'),
  fileMeta: document.getElementById('file-meta'),
};

const statusOrder = ['pending', 'in_progress', 'completed', 'blocked'];
const statusLabels = {
  pending: 'Pending',
  in_progress: 'In Progress',
  completed: 'Completed',
  blocked: 'Blocked',
};

function fmtTs(ts) {
  if (!ts) return '--';
  return new Date(ts * 1000).toLocaleTimeString();
}

function badge(status) {
  return `<span class="badge ${status}">${status.replace('_', ' ')}</span>`;
}

function escapeHtml(text) {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function escapeAttr(text) {
  return escapeHtml(text).replaceAll('"', '&quot;');
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

async function refreshState() {
  const query = state.selectedRunId ? `?run_id=${encodeURIComponent(state.selectedRunId)}` : '';
  const data = await fetchJSON(`/api/state${query}`);
  state.data = data;
  if (!state.selectedRunId && data.current_run) {
    state.selectedRunId = data.current_run.id;
  }
  render();
}

function renderOverview() {
  const current = state.data?.current_run;
  const tasks = state.data?.tasks || [];
  const worktrees = state.data?.worktrees || [];
  const jobs = state.data?.jobs || [];
  const completed = tasks.filter((task) => task.status === 'completed').length;
  const runningJobs = jobs.filter((job) => job.status === 'running').length;
  els.overview.innerHTML = [
    ['Active Run', current ? current.slug : 'None'],
    ['Tasks Done', `${completed}/${tasks.length || 0}`],
    ['Live Worktrees', worktrees.filter((wt) => wt.status !== 'removed').length],
    ['Running Jobs', runningJobs],
  ]
    .map(([label, value]) => `
      <article class="stat">
        <div class="stat-label muted">${label}</div>
        <div class="stat-value">${value}</div>
      </article>`)
    .join('');
}

function renderRuns() {
  const runs = state.data?.runs || [];
  els.runSelect.innerHTML = runs
    .map((run) => `
      <option value="${run.id}" ${run.id === state.selectedRunId ? 'selected' : ''}>
        ${run.slug} · ${run.status}
      </option>`)
    .join('');
}

function renderTasks() {
  const tasks = state.data?.tasks || [];
  const columns = statusOrder.map((status) => {
    const items = tasks.filter((task) => task.status === status);
    const cards = items.length
      ? items
          .map((task) => `
            <article class="card">
              <div class="card-title">#${task.id} ${task.subject}</div>
              <div class="card-meta inline-meta">
                ${badge(task.status)}
                ${task.owner ? `<span class="badge">${task.owner}</span>` : ''}
              </div>
              <div class="card-meta">${task.description}</div>
              ${task.worktree ? `<div class="card-meta task-worktree" title="${escapeAttr(task.worktree)}">${escapeHtml(task.worktree)}</div>` : ''}
              ${task.error ? `<div class="card-meta">${escapeHtml(task.error)}</div>` : ''}
            </article>`)
          .join('')
      : '<div class="muted">No tasks</div>';
    return `
      <section class="column">
        <h3>${statusLabels[status]}</h3>
        ${cards}
      </section>`;
  });
  els.taskColumns.innerHTML = columns.join('');
}

function renderTimeline() {
  const events = [...(state.data?.events || [])].reverse();
  els.timeline.innerHTML = events.length
    ? events
        .map((event) => `
          <article class="event">
            <div class="card-title">${event.type}</div>
            <div class="event-meta">
              ${event.agent ? `agent=${event.agent} · ` : ''}
              ${event.task_id ? `task=#${event.task_id} · ` : ''}
              ${fmtTs(event.ts)}
            </div>
            <div class="event-meta">${renderEventDetails(event)}</div>
          </article>`)
        .join('')
    : '<div class="muted">No events yet.</div>';
}

function renderEventDetails(event) {
  if (event.payload?.message) return event.payload.message;
  if (event.detail) return event.detail;
  if (event.command) return event.command;
  if (event.paths) return event.paths.join(', ');
  if (event.summary_path) return event.summary_path;
  if (event.worktree?.name) return event.worktree.name;
  return 'State transition recorded.';
}

function renderAgents() {
  const agents = Object.values(state.data?.current_run?.agents || {});
  els.agents.innerHTML = agents.length
    ? agents
        .map((agent) => `
          <article class="agent-card">
            <div class="agent-name">${agent.name}</div>
            <div class="card-meta">${badge(agent.state)} <span class="badge">${agent.role}</span></div>
            <div class="agent-detail">${agent.detail || 'Waiting.'}</div>
            <div class="card-meta">${agent.worktree ? `worktree=${agent.worktree}` : 'no worktree yet'}</div>
          </article>`)
        .join('')
    : '<div class="muted">No agent state yet.</div>';
}

async function renderWorktrees() {
  const worktrees = state.data?.worktrees || [];
  if (!worktrees.length) {
    els.worktrees.innerHTML = '<div class="muted">No worktrees for this run.</div>';
    els.artifactList.innerHTML = '<div class="muted">No artifact files yet.</div>';
    return;
  }

  const cardHtml = worktrees
    .map((wt) => `
      <article class="worktree-card">
        <div class="worktree-name" title="${escapeAttr(wt.name)}">${escapeHtml(wt.name)}</div>
        <div class="card-meta inline-meta">${badge(wt.status)} <span class="badge">task #${wt.task_id}</span></div>
        <div class="worktree-path" title="${escapeAttr(wt.path)}">${escapeHtml(wt.path)}</div>
        <div class="worktree-actions">
          <button class="secondary" data-action="keep" data-name="${wt.name}">Keep</button>
          <button class="danger" data-action="remove" data-name="${wt.name}">Remove</button>
        </div>
      </article>`)
    .join('');
  els.worktrees.innerHTML = cardHtml;

  const fileGroups = await Promise.all(
    worktrees.map(async (wt) => {
      const data = await fetchJSON(`/api/worktrees/${encodeURIComponent(wt.name)}/files`);
      return { worktree: wt, items: data.items || [] };
    })
  );

  const artifactCards = [];
  for (const group of fileGroups) {
    for (const item of group.items) {
      const absolute = `${group.worktree.path}/${item.path}`;
      const label = `${group.worktree.name} · ${item.path}`;
      artifactCards.push(`
        <div class="artifact-item ${state.selectedFilePath === absolute ? 'active' : ''}" data-path="${absolute}">
          <div class="card-title artifact-title" title="${escapeAttr(label)}">${escapeHtml(label)}</div>
          <div class="card-meta">${item.size} bytes</div>
        </div>`);
    }
  }
  els.artifactList.innerHTML = artifactCards.join('') || '<div class="muted">No files found.</div>';
}

function renderJobs() {
  const jobs = state.data?.jobs || [];
  els.jobs.innerHTML = jobs.length
    ? jobs
        .map((job) => `
          <article class="job-card">
            <div class="job-title">${job.agent} · ${badge(job.status)}</div>
            <div class="job-command" title="${escapeAttr(job.command)}">${escapeHtml(job.command)}</div>
            <div class="card-meta mono" title="${escapeAttr(job.cwd)}">cwd=${escapeHtml(job.cwd)}</div>
            ${job.result ? `<details><summary>Output</summary><pre>${escapeHtml(job.result)}</pre></details>` : ''}
          </article>`)
        .join('')
    : '<div class="muted">No background jobs for this run.</div>';
}

function render() {
  renderOverview();
  renderRuns();
  renderTasks();
  renderTimeline();
  renderAgents();
  renderJobs();
  renderWorktrees();
}

async function loadFile(path) {
  try {
    const data = await fetchJSON(`/api/file?path=${encodeURIComponent(path)}`);
    state.selectedFilePath = path;
    els.fileMeta.textContent = `${data.path} · ${data.size} chars`;
    els.fileContent.textContent = data.content;
    document.querySelectorAll('.artifact-item').forEach((node) => {
      node.classList.toggle('active', node.dataset.path === path);
    });
  } catch (error) {
    els.fileMeta.textContent = 'Failed to load file';
    els.fileContent.textContent = error.message;
  }
}

function connectWebSocket() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${window.location.host}/ws`);
  ws.onmessage = async (message) => {
    const event = JSON.parse(message.data);
    if (event.type === 'hello') return;
    const currentRun = state.selectedRunId;
    if (!currentRun || event.run_id === currentRun) {
      await refreshState();
    }
  };
  ws.onclose = () => {
    setTimeout(connectWebSocket, 1200);
  };
}

els.form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const mission = els.input.value.trim();
  if (!mission) {
    els.formStatus.textContent = 'Mission is required.';
    return;
  }
  els.startBtn.disabled = true;
  els.formStatus.textContent = 'Starting mission run...';
  try {
    const run = await fetchJSON('/api/missions', {
      method: 'POST',
      body: JSON.stringify({ mission }),
    });
    state.selectedRunId = run.id;
    els.formStatus.textContent = `Run ${run.slug} started.`;
    els.input.value = '';
    await refreshState();
  } catch (error) {
    els.formStatus.textContent = error.message;
  } finally {
    els.startBtn.disabled = false;
  }
});

els.runSelect.addEventListener('change', async (event) => {
  state.selectedRunId = event.target.value;
  await refreshState();
});

document.addEventListener('click', async (event) => {
  const artifact = event.target.closest('.artifact-item');
  if (artifact?.dataset.path) {
    await loadFile(artifact.dataset.path);
    return;
  }

  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const { action, name } = button.dataset;
  button.disabled = true;
  try {
    if (action === 'keep') {
      await fetchJSON(`/api/worktrees/${encodeURIComponent(name)}/keep`, { method: 'POST' });
    }
    if (action === 'remove') {
      await fetchJSON(`/api/worktrees/${encodeURIComponent(name)}`, { method: 'DELETE' });
    }
    await refreshState();
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
  }
});

(async function boot() {
  connectWebSocket();
  await refreshState();
})();
