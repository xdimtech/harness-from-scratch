const state = {
  runs: [],
  selectedRunId: null,
  selectedTaskId: null,
  selectedFilePath: null,
  selectedWorktreeId: null,
  snapshot: null,
  worktreeFiles: [],
};

const els = {
  form: document.getElementById('run-form'),
  repoRoot: document.getElementById('repo-root'),
  mission: document.getElementById('mission'),
  constraints: document.getElementById('constraints'),
  validation: document.getElementById('validation'),
  executionMode: document.getElementById('execution-mode'),
  agentCommand: document.getElementById('agent-command'),
  agentTimeout: document.getElementById('agent-timeout'),
  executionNote: document.getElementById('execution-note'),
  formStatus: document.getElementById('form-status'),
  createRunBtn: document.getElementById('create-run-btn'),
  runsList: document.getElementById('runs-list'),
  runTitle: document.getElementById('run-title'),
  runSummary: document.getElementById('run-summary'),
  runMeta: document.getElementById('run-meta'),
  approveRunBtn: document.getElementById('approve-run-btn'),
  pauseRunBtn: document.getElementById('pause-run-btn'),
  statsRow: document.getElementById('stats-row'),
  taskBoard: document.getElementById('task-board'),
  taskDetail: document.getElementById('task-detail'),
  taskDetailMeta: document.getElementById('task-detail-meta'),
  timeline: document.getElementById('timeline'),
  agents: document.getElementById('agents'),
  jobs: document.getElementById('jobs'),
  worktrees: document.getElementById('worktrees'),
  artifactList: document.getElementById('artifact-list'),
  fileContent: document.getElementById('file-content'),
  inspectorMeta: document.getElementById('inspector-meta'),
};

const defaultMeta = {
  default_execution_mode: 'scaffold',
  default_agent_command: '',
  default_agent_timeout_seconds: 900,
  default_agent_source: 'none',
  real_agent_available: false,
  default_model_id: '',
  default_base_url: '',
};

const taskColumns = [
  { key: 'todo', label: 'Planned' },
  { key: 'ready', label: 'Ready' },
  { key: 'executing', label: 'Executing' },
  { key: 'review', label: 'Done / Needs Attention' },
];

function escapeHtml(text) {
  return String(text)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function escapeAttr(text) {
  return escapeHtml(text).replaceAll('"', '&quot;');
}

function badge(status) {
  return `<span class="badge ${status}">${escapeHtml(String(status).replaceAll('_', ' '))}</span>`;
}

function fmtTime(ts) {
  if (!ts) return '--';
  return new Date(ts * 1000).toLocaleString();
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

function toggleExecutionInputs() {
  const mode = els.executionMode.value;
  const commandEnabled = mode === 'agent_command';
  const timeoutEnabled = mode !== 'scaffold';
  els.agentCommand.disabled = !commandEnabled;
  els.agentTimeout.disabled = !timeoutEnabled;
  if (mode === 'direct_llm') {
    els.executionNote.textContent = 'RepoPilot will drive the LLM itself through its own tool runtime and write code inside each isolated workspace.';
    return;
  }
  if (mode === 'agent_command') {
    els.executionNote.textContent = 'RepoPilot will hand the task to an external executor command inside each isolated workspace.';
    return;
  }
  els.executionNote.textContent = 'Scaffold mode does not run a coding agent. Use it only to debug planning or workspace creation.';
}

async function refreshRuns() {
  const data = await fetchJSON('/api/runs');
  state.runs = data.items || [];
  if (!state.selectedRunId && state.runs.length) {
    state.selectedRunId = state.runs[0].id;
  }
  renderRuns();
  if (state.selectedRunId) {
    await refreshSnapshot();
  } else {
    renderEmptySnapshot();
  }
}

async function refreshSnapshot() {
  if (!state.selectedRunId) return;
  state.snapshot = await fetchJSON(`/api/runs/${encodeURIComponent(state.selectedRunId)}`);
  const tasks = state.snapshot.tasks || [];
  if (!tasks.find((task) => task.id === state.selectedTaskId)) {
    state.selectedTaskId = tasks[0]?.id || null;
  }
  renderSnapshot();
}

function renderRuns() {
  if (!state.runs.length) {
    els.runsList.innerHTML = '<div class="empty">No runs yet.</div>';
    return;
  }
  els.runsList.innerHTML = state.runs
    .map((run) => `
      <button class="run-item ${run.id === state.selectedRunId ? 'active' : ''}" data-run-id="${run.id}">
        <div class="card-title">${escapeHtml(run.slug)}</div>
        <div class="inline-meta">${badge(run.status)} ${badge(run.execution?.mode || 'scaffold')}</div>
        <div class="card-meta">${escapeHtml(run.mission)}</div>
      </button>`)
    .join('');
}

function renderEmptySnapshot() {
  els.runTitle.textContent = 'Select or create a run';
  els.runSummary.textContent = 'Review the generated task graph, approve execution, and inspect jobs, workspaces, artifacts, and files from one place.';
  els.runMeta.innerHTML = '';
  els.statsRow.innerHTML = '';
  els.taskBoard.innerHTML = '<div class="empty">No run selected.</div>';
  els.taskDetail.innerHTML = 'No task selected.';
  els.taskDetailMeta.textContent = 'Select a task';
  els.timeline.innerHTML = '<div class="empty">No events yet.</div>';
  els.agents.innerHTML = '<div class="empty">No agent state yet.</div>';
  els.jobs.innerHTML = '<div class="empty">No jobs yet.</div>';
  els.worktrees.innerHTML = '<div class="empty">No workspaces yet.</div>';
  els.artifactList.innerHTML = '<div class="empty">No artifacts yet.</div>';
  els.fileContent.textContent = 'No file selected.';
  els.inspectorMeta.textContent = 'Select an artifact or workspace file';
  els.approveRunBtn.disabled = true;
  els.pauseRunBtn.disabled = true;
}

function renderStats(run, tasks, jobs, worktrees) {
  const completed = tasks.filter((task) => task.status === 'completed').length;
  const failed = tasks.filter((task) => task.status === 'failed').length;
  const runningJobs = jobs.filter((job) => ['queued', 'running'].includes(job.status)).length;
  const cards = [
    ['Run Status', run.status],
    ['Tasks Completed', `${completed}/${tasks.length || 0}`],
    ['Tasks Failed', String(failed)],
    ['Active Jobs', String(runningJobs)],
    ['Workspaces', String(worktrees.length)],
  ];
  els.statsRow.innerHTML = cards
    .map(([label, value]) => `
      <article class="stat-card">
        <div class="stat-label">${escapeHtml(label)}</div>
        <div class="stat-value">${escapeHtml(value)}</div>
      </article>`)
    .join('');
}

function taskColumnKey(task) {
  if (['completed', 'review_required', 'failed', 'blocked', 'canceled'].includes(task.status)) return 'review';
  if (['executing', 'claimed', 'validating'].includes(task.status)) return 'executing';
  if (task.status === 'ready') return 'ready';
  return 'todo';
}

function renderTaskBoard(tasks) {
  els.taskBoard.innerHTML = taskColumns
    .map((column) => {
      const cards = tasks.filter((task) => taskColumnKey(task) === column.key);
      return `
        <section class="task-column">
          <h3>${escapeHtml(column.label)}</h3>
          ${cards.length ? cards.map(renderTaskCard).join('') : '<div class="empty">No tasks</div>'}
        </section>`;
    })
    .join('');
}

function renderTaskCard(task) {
  const retryButton = ['failed', 'blocked', 'review_required'].includes(task.status)
    ? `<button class="secondary" data-retry-task="${task.id}">Retry</button>`
    : '';
  return `
    <button class="task-card ${task.id === state.selectedTaskId ? 'active' : ''}" data-task-id="${task.id}">
      <div class="card-title">#${task.id} ${escapeHtml(task.title)}</div>
      <div class="inline-meta">
        ${badge(task.status)}
        <span class="badge">${escapeHtml(task.role_required)}</span>
      </div>
      <div class="card-meta">${escapeHtml(task.phase || task.description)}</div>
      ${task.worktree_id ? `<div class="card-meta">workspace linked</div>` : ''}
      ${task.error ? `<div class="card-meta">Error: ${escapeHtml(task.error)}</div>` : ''}
      ${retryButton}
    </button>`;
}

function renderTaskDetail(task, jobs, worktrees) {
  if (!task) {
    els.taskDetail.innerHTML = 'No task selected.';
    els.taskDetailMeta.textContent = 'Select a task';
    return;
  }
  const taskJobs = jobs.filter((job) => job.task_id === task.id);
  const worktree = worktrees.find((item) => item.id === task.worktree_id);
  els.taskDetailMeta.textContent = `Task #${task.id}`;
  els.taskDetail.innerHTML = `
    <div class="detail-block">
      <div class="detail-title">${escapeHtml(task.title)}</div>
      <div class="inline-meta">${badge(task.status)} <span class="badge">${escapeHtml(task.kind)}</span> <span class="badge">${escapeHtml(task.role_required)}</span></div>
      <div class="detail-meta">${escapeHtml(task.description)}</div>
      <div class="detail-meta">Phase: ${escapeHtml(task.phase || '--')}</div>
      ${task.error ? `<div class="detail-meta">Error: ${escapeHtml(task.error)}</div>` : ''}
      ${task.depends_on?.length ? `<div class="detail-meta">Depends on: ${task.depends_on.join(', ')}</div>` : ''}
    </div>
    <div class="detail-block">
      <div class="detail-title">Acceptance Criteria</div>
      ${task.acceptance_criteria?.length ? `<ul>${task.acceptance_criteria.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '<div class="empty">No acceptance criteria.</div>'}
    </div>
    <div class="detail-block">
      <div class="detail-title">Commands</div>
      ${task.commands?.length ? `<ul>${task.commands.map((item) => `<li><code>${escapeHtml(item)}</code></li>`).join('')}</ul>` : '<div class="empty">No commands attached.</div>'}
    </div>
    <div class="detail-block">
      <div class="detail-title">Workspace</div>
      ${worktree ? `
        <div class="detail-meta">${escapeHtml(worktree.path)}</div>
        <div class="inline-meta">${badge(worktree.status)} <span class="badge">${escapeHtml(worktree.workspace_type || 'git_worktree')}</span></div>
        <button class="secondary" data-browse-worktree="${worktree.id}">Browse files</button>
      ` : '<div class="empty">No workspace attached.</div>'}
    </div>
    <div class="detail-block">
      <div class="detail-title">Jobs</div>
      ${taskJobs.length ? taskJobs.map((job) => `<div class="detail-meta">${escapeHtml(job.command)} -> ${escapeHtml(job.status)}</div>`).join('') : '<div class="empty">No jobs recorded for this task yet.</div>'}
    </div>`;
}

function renderTimeline(events) {
  if (!events.length) {
    els.timeline.innerHTML = '<div class="empty">No events yet.</div>';
    return;
  }
  els.timeline.innerHTML = [...events]
    .reverse()
    .map((event) => `
      <article class="event-card">
        <div class="card-title">${escapeHtml(event.type)}</div>
        <div class="event-meta">${fmtTime(event.ts)} ${event.task_id ? `| task #${event.task_id}` : ''}</div>
        <div class="event-meta">${escapeHtml(event.detail || event.summary || event.command || event.error || event.line || event.path || '')}</div>
      </article>`)
    .join('');
}

function renderAgents(run) {
  const agents = Object.values(run.agents || {});
  if (!agents.length) {
    els.agents.innerHTML = '<div class="empty">No agent state yet.</div>';
    return;
  }
  els.agents.innerHTML = agents
    .map((agent) => `
      <article class="agent-card">
        <div class="agent-title">${escapeHtml(agent.id)}</div>
        <div class="inline-meta">${badge(agent.state)} <span class="badge">${escapeHtml(agent.role)}</span></div>
        <div class="card-meta">${escapeHtml(agent.detail || '')}</div>
        <div class="card-meta">Updated: ${fmtTime(agent.updated_at)}</div>
      </article>`)
    .join('');
}

function renderJobs(jobs) {
  if (!jobs.length) {
    els.jobs.innerHTML = '<div class="empty">No jobs yet.</div>';
    return;
  }
  els.jobs.innerHTML = jobs
    .map((job) => `
      <article class="job-card">
        <div class="job-title">${badge(job.status)} ${escapeHtml(job.command)}</div>
        <div class="job-meta">cwd=${escapeHtml(job.cwd)}</div>
        <div class="job-meta">started=${escapeHtml(fmtTime(job.started_at))} | finished=${escapeHtml(fmtTime(job.finished_at))}</div>
        <details>
          <summary>Output</summary>
          <pre>${escapeHtml(job.output || '(no output)')}</pre>
        </details>
      </article>`)
    .join('');
}

function renderWorktrees(worktrees) {
  if (!worktrees.length) {
    els.worktrees.innerHTML = '<div class="empty">No workspaces yet.</div>';
    return;
  }
  els.worktrees.innerHTML = worktrees
    .map((item) => `
      <article class="worktree-card ${item.id === state.selectedWorktreeId ? 'active' : ''}">
        <div class="worktree-title">${escapeHtml(item.name)}</div>
        <div class="inline-meta">${badge(item.status)} <span class="badge">${escapeHtml(item.workspace_type || 'git_worktree')}</span></div>
        <div class="worktree-meta">${escapeHtml(item.path)}</div>
        <div class="inline-meta">
          <button class="secondary" data-browse-worktree="${item.id}">Browse files</button>
        </div>
      </article>`)
    .join('');
}

function renderArtifacts(snapshot) {
  const artifacts = snapshot.artifacts || [];
  const worktreeFiles = state.worktreeFiles || [];
  const cards = [];
  for (const artifact of artifacts) {
    cards.push(`
      <button class="artifact-item ${state.selectedFilePath === artifact.path ? 'active' : ''}" data-file-path="${escapeAttr(artifact.path)}">
        <div class="card-title">Task #${artifact.task_id} | ${escapeHtml(artifact.name)}</div>
        <div class="card-meta">${escapeHtml(artifact.path)}</div>
      </button>`);
  }
  for (const item of worktreeFiles) {
    cards.push(`
      <button class="artifact-item ${state.selectedFilePath === item.absolute_path ? 'active' : ''}" data-file-path="${escapeAttr(item.absolute_path)}">
        <div class="card-title">Workspace file | ${escapeHtml(item.path)}</div>
        <div class="card-meta">${escapeHtml(item.absolute_path)} | ${item.size} bytes</div>
      </button>`);
  }
  els.artifactList.innerHTML = cards.join('') || '<div class="empty">No artifacts or files yet.</div>';
}

async function browseWorktree(worktreeId) {
  state.selectedWorktreeId = worktreeId;
  const data = await fetchJSON(`/api/worktrees/${encodeURIComponent(worktreeId)}/files`);
  state.worktreeFiles = data.items || [];
  renderWorktrees(state.snapshot.worktrees || []);
  renderArtifacts(state.snapshot);
}

async function loadFile(path) {
  const data = await fetchJSON(`/api/file?path=${encodeURIComponent(path)}`);
  state.selectedFilePath = path;
  els.inspectorMeta.textContent = `${data.path} | ${data.size} chars`;
  els.fileContent.textContent = data.content;
  document.querySelectorAll('[data-file-path]').forEach((node) => {
    node.classList.toggle('active', node.dataset.filePath === path);
  });
}

function renderRunHeader(run) {
  const execution = run.execution || { mode: 'scaffold', agent_command: '', agent_timeout_seconds: 900 };
  els.runTitle.textContent = run.mission;
  els.runSummary.textContent = run.plan?.summary || 'No planner summary available.';
  els.runMeta.innerHTML = [
    badge(run.status),
    badge(execution.mode),
    `<span class="badge">repo ${escapeHtml(run.repo_root)}</span>`,
    `<span class="badge">timeout ${escapeHtml(String(execution.agent_timeout_seconds || 900))}s</span>`,
  ].join(' ');
  if (run.last_error) {
    els.runMeta.innerHTML += ` <span class="badge failed">${escapeHtml(run.last_error)}</span>`;
  }
  els.approveRunBtn.disabled = !['planned', 'failed', 'paused'].includes(run.status);
  els.pauseRunBtn.disabled = !['approved', 'running'].includes(run.status);
}

function renderSnapshot() {
  const run = state.snapshot.run;
  const tasks = state.snapshot.tasks || [];
  const jobs = state.snapshot.jobs || [];
  const worktrees = state.snapshot.worktrees || [];
  const events = state.snapshot.events || [];
  const selectedTask = tasks.find((task) => task.id === state.selectedTaskId) || tasks[0] || null;
  if (selectedTask) {
    state.selectedTaskId = selectedTask.id;
  }

  renderRunHeader(run);
  renderStats(run, tasks, jobs, worktrees);
  renderTaskBoard(tasks);
  renderTaskDetail(selectedTask, jobs, worktrees);
  renderTimeline(events);
  renderAgents(run);
  renderJobs(jobs);
  renderWorktrees(worktrees);
  renderArtifacts(state.snapshot);
}

function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${protocol}//${window.location.host}/ws/events`);
  ws.onmessage = async (message) => {
    const event = JSON.parse(message.data);
    if (event.type === 'hello') return;
    if (!state.selectedRunId || event.run_id === state.selectedRunId) {
      await refreshRuns();
    }
  };
  ws.onclose = () => setTimeout(connectWebSocket, 1200);
}

els.executionMode.addEventListener('change', toggleExecutionInputs);

els.form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const mission = els.mission.value.trim();
  const repoRoot = els.repoRoot.value.trim();
  if (!repoRoot || !mission) {
    els.formStatus.textContent = 'Repository path and mission are required.';
    return;
  }
  els.createRunBtn.disabled = true;
  els.formStatus.textContent = 'Creating planned run...';
  try {
    const validationCommands = els.validation.value
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean);
    const payload = {
      repo_root: repoRoot,
      mission,
      constraints: els.constraints.value,
      validation_commands: validationCommands,
      execution_mode: els.executionMode.value,
      agent_command: els.agentCommand.value.trim(),
      agent_timeout_seconds: Number(els.agentTimeout.value || 900),
    };
    if (payload.execution_mode === 'agent_command' && !payload.agent_command) {
      throw new Error('Agent command is required for real execution. Install codex or fill the command field.');
    }
    const run = await fetchJSON('/api/runs', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    state.selectedRunId = run.id;
    state.selectedTaskId = null;
    state.selectedWorktreeId = null;
    state.selectedFilePath = null;
    state.worktreeFiles = [];
    els.formStatus.textContent = `Planned run ${run.slug} created.`;
    await refreshRuns();
  } catch (error) {
    els.formStatus.textContent = error.message;
  } finally {
    els.createRunBtn.disabled = false;
  }
});

els.approveRunBtn.addEventListener('click', async () => {
  if (!state.selectedRunId) return;
  els.approveRunBtn.disabled = true;
  try {
    await fetchJSON(`/api/runs/${encodeURIComponent(state.selectedRunId)}/approve`, { method: 'POST' });
    await refreshRuns();
  } catch (error) {
    alert(error.message);
  }
});

els.pauseRunBtn.addEventListener('click', async () => {
  if (!state.selectedRunId) return;
  try {
    await fetchJSON(`/api/runs/${encodeURIComponent(state.selectedRunId)}/pause`, { method: 'POST' });
    await refreshRuns();
  } catch (error) {
    alert(error.message);
  }
});

document.addEventListener('click', async (event) => {
  const runNode = event.target.closest('[data-run-id]');
  if (runNode) {
    state.selectedRunId = runNode.dataset.runId;
    state.selectedTaskId = null;
    state.selectedWorktreeId = null;
    state.selectedFilePath = null;
    state.worktreeFiles = [];
    await refreshRuns();
    return;
  }

  const taskNode = event.target.closest('[data-task-id]');
  if (taskNode) {
    state.selectedTaskId = Number(taskNode.dataset.taskId);
    renderSnapshot();
    return;
  }

  const artifact = event.target.closest('[data-file-path]');
  if (artifact) {
    await loadFile(artifact.dataset.filePath);
    return;
  }

  const retry = event.target.closest('[data-retry-task]');
  if (retry) {
    await fetchJSON(`/api/tasks/${retry.dataset.retryTask}/retry`, { method: 'POST' });
    await refreshRuns();
    return;
  }

  const browse = event.target.closest('[data-browse-worktree]');
  if (browse) {
    await browseWorktree(browse.dataset.browseWorktree);
  }
});

(async function boot() {
  try {
    const meta = { ...defaultMeta, ...(await fetchJSON('/api/meta')) };
    els.repoRoot.value = meta.default_repo_root || '';
    els.executionMode.value = meta.default_execution_mode || 'scaffold';
    els.agentCommand.value = meta.default_agent_command || '';
    els.agentTimeout.value = String(meta.default_agent_timeout_seconds || 900);
    if (meta.default_execution_mode === 'direct_llm') {
      const proxyNote = meta.default_base_url ? ` via ${meta.default_base_url}` : '';
      els.formStatus.textContent = `Direct LLM agent ready with model ${meta.default_model_id || '(unknown)'}${proxyNote}.`;
    } else if (meta.real_agent_available) {
      els.formStatus.textContent = meta.default_agent_source === 'env'
        ? 'External agent default loaded from REPOPILOT_AGENT_COMMAND.'
        : 'External agent default auto-detected from local codex installation.';
    } else {
      els.formStatus.textContent = 'No real agent auto-detected yet. Install codex or configure REPOPILOT_AGENT_COMMAND.';
    }
  } catch (error) {
    els.repoRoot.value = '';
    els.formStatus.textContent = 'Failed to load runtime defaults.';
  }
  toggleExecutionInputs();
  connectWebSocket();
  await refreshRuns();
})();
