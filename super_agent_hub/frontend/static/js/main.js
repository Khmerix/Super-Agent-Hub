/**
 * Super Agent Hub - Frontend Bridge
 * Connects to FastAPI backend via REST + WebSocket.
 */

const API_BASE = window.location.origin;
let ws = null;
let runs = [];

// ── Init ────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initWebSocket();
    loadAgents();
    loadRuns();
    setInterval(loadAgents, 3000);
    setInterval(loadRuns, 5000);

    document.getElementById('btn-submit').addEventListener('click', submitTask);
});

// ── WebSocket ────────────────────────────────────────────────
function initWebSocket() {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${window.location.host}/ws`);

    ws.onopen = () => {
        log('WebSocket connected');
        ws.send(JSON.stringify({ action: 'status' }));
    };

    ws.onmessage = (evt) => {
        const msg = JSON.parse(evt.data);
        handleWsMessage(msg);
    };

    ws.onclose = () => {
        log('WebSocket disconnected — reconnecting in 3s...');
        setTimeout(initWebSocket, 3000);
    };
}

function handleWsMessage(msg) {
    if (msg.type === 'agents') {
        renderAgents(msg.data);
    } else if (msg.event) {
        log(`[${msg.event}] run=${msg.run_id} node=${msg.node}`);
        if (msg.completed) {
            loadRuns();
        }
    }
}

// ── Agents ─────────────────────────────────────────────────
async function loadAgents() {
    try {
        const res = await fetch(`${API_BASE}/api/agents/status`);
        const data = await res.json();
        renderAgents(data.agents);
    } catch (e) {
        console.error('Failed to load agents', e);
    }
}

function renderAgents(agents) {
    const container = document.getElementById('agent-cards');
    if (!agents || !agents.length) {
        container.innerHTML = '<div class="card">No agents registered</div>';
        return;
    }
    container.innerHTML = agents.map(a => `
        <div class="card">
            <div class="name">${a.name}</div>
            <div class="desc">${a.description || ''}</div>
            <span class="status ${a.status}">${a.status}</span>
            <div class="stats">Runs: ${a.executions} | Fail: ${a.failures} | Model: ${a.model || 'N/A'}</div>
        </div>
    `).join('');
}

// ── Task Submit ────────────────────────────────────────────
async function submitTask() {
    const title = document.getElementById('task-title').value.trim();
    const desc = document.getElementById('task-desc').value.trim();
    const priority = document.getElementById('task-priority').value;
    const btn = document.getElementById('btn-submit');

    if (!title || !desc) {
        alert('Please enter both title and description');
        return;
    }

    btn.disabled = true;
    try {
        const res = await fetch(`${API_BASE}/api/tasks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, description: desc, priority })
        });
        const data = await res.json();
        log(`Task submitted: ${data.run_id}`);
        document.getElementById('task-title').value = '';
        document.getElementById('task-desc').value = '';
        loadRuns();

        // Open SSE stream for this run
        openRunStream(data.run_id);
    } catch (e) {
        log(`Submit error: ${e.message}`);
    } finally {
        btn.disabled = false;
    }
}

// ── Runs ───────────────────────────────────────────────────
async function loadRuns() {
    try {
        // Fetch from recorder directly via a trace endpoint
        // For now, we rely on the UI keeping state; a real app would have /api/runs
        renderRuns();
    } catch (e) {
        console.error(e);
    }
}

function renderRuns() {
    const container = document.getElementById('runs-list');
    if (!runs.length) {
        container.innerHTML = '<div class="run-item empty">No runs yet</div>';
        return;
    }
    container.innerHTML = runs.map(r => `
        <div class="run-item">
            <div>
                <div class="run-title">${r.title || 'Untitled'}</div>
                <div class="run-meta">${r.run_id} • ${r.created_at || ''}</div>
            </div>
            <span class="run-status ${r.status}">${r.status}</span>
        </div>
    `).join('');
}

// ── SSE Stream for a specific run ──────────────────────────
function openRunStream(runId) {
    const evtSource = new EventSource(`${API_BASE}/api/tasks/${runId}/stream`);
    evtSource.onmessage = (e) => {
        const data = JSON.parse(e.data);
        log(`[SSE ${runId}] ${data.event} | node=${data.node}`);
        if (data.completed || data.event === 'timeout') {
            evtSource.close();
            loadRuns();
        }
    };
    evtSource.onerror = () => {
        evtSource.close();
    };
}

// ── Logger ─────────────────────────────────────────────────
function log(text) {
    const el = document.getElementById('live-log');
    const entry = document.createElement('div');
    entry.className = 'entry new';
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
    el.appendChild(entry);
    el.scrollTop = el.scrollHeight;
    setTimeout(() => entry.classList.remove('new'), 500);
}
