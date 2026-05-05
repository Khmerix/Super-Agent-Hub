/**
 * SUPER AGENT HUB — Frontend Bridge
 * Cyberpunk dashboard with particle background, 3D card tilts, neon animations.
 */

const API_BASE = window.location.origin;
let ws = null;
let runs = [];
let particles = [];

// ═══════════════════════════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    initParticles();
    initWebSocket();
    loadAgents();
    loadRuns();

    setInterval(loadAgents, 3000);
    setInterval(loadRuns, 5000);

    document.getElementById('btn-submit').addEventListener('click', submitTask);

    // Add 3D tilt to all panels on mouse move
    document.querySelectorAll('.panel-glass').forEach(panel => {
        panel.addEventListener('mousemove', handlePanelTilt);
        panel.addEventListener('mouseleave', resetPanelTilt);
    });
});

// ═══════════════════════════════════════════════════════════════
//  PARTICLE BACKGROUND (Canvas 2D)
// ═══════════════════════════════════════════════════════════════

function initParticles() {
    const canvas = document.getElementById('bg-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener('resize', resize);

    const PARTICLE_COUNT = 60;
    particles = [];
    for (let i = 0; i < PARTICLE_COUNT; i++) {
        particles.push({
            x: Math.random() * canvas.width,
            y: Math.random() * canvas.height,
            vx: (Math.random() - 0.5) * 0.3,
            vy: (Math.random() - 0.5) * 0.3,
            radius: Math.random() * 1.5 + 0.5,
            alpha: Math.random() * 0.5 + 0.2,
            hue: 270 + Math.random() * 40 // purple range
        });
    }

    function draw() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // Draw connections
        for (let i = 0; i < particles.length; i++) {
            for (let j = i + 1; j < particles.length; j++) {
                const dx = particles[i].x - particles[j].x;
                const dy = particles[i].y - particles[j].y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < 150) {
                    const alpha = (1 - dist / 150) * 0.12;
                    ctx.beginPath();
                    ctx.moveTo(particles[i].x, particles[i].y);
                    ctx.lineTo(particles[j].x, particles[j].y);
                    ctx.strokeStyle = `rgba(168, 85, 247, ${alpha})`;
                    ctx.lineWidth = 0.5;
                    ctx.stroke();
                }
            }
        }

        // Draw particles
        for (const p of particles) {
            p.x += p.vx;
            p.y += p.vy;

            if (p.x < 0) p.x = canvas.width;
            if (p.x > canvas.width) p.x = 0;
            if (p.y < 0) p.y = canvas.height;
            if (p.y > canvas.height) p.y = 0;

            ctx.beginPath();
            ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
            ctx.fillStyle = `hsla(${p.hue}, 80%, 65%, ${p.alpha})`;
            ctx.fill();

            // Glow
            ctx.beginPath();
            ctx.arc(p.x, p.y, p.radius * 3, 0, Math.PI * 2);
            ctx.fillStyle = `hsla(${p.hue}, 80%, 65%, ${p.alpha * 0.15})`;
            ctx.fill();
        }

        requestAnimationFrame(draw);
    }
    draw();
}

// ═══════════════════════════════════════════════════════════════
//  3D PANEL TILT
// ═══════════════════════════════════════════════════════════════

function handlePanelTilt(e) {
    const panel = e.currentTarget;
    const rect = panel.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const cx = rect.width / 2;
    const cy = rect.height / 2;

    const rx = ((y - cy) / cy) * -3;
    const ry = ((x - cx) / cx) * 3;

    panel.style.transform = `perspective(800px) rotateX(${rx}deg) rotateY(${ry}deg) scale3d(1.01, 1.01, 1.01)`;
    panel.style.transition = 'transform 0.1s ease-out';
}

function resetPanelTilt(e) {
    const panel = e.currentTarget;
    panel.style.transform = 'perspective(800px) rotateX(0) rotateY(0) scale3d(1, 1, 1)';
    panel.style.transition = 'transform 0.4s ease-out';
}

// ═══════════════════════════════════════════════════════════════
//  WEBSOCKET
// ═══════════════════════════════════════════════════════════════

function initWebSocket() {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${window.location.host}/ws`);

    ws.onopen = () => {
        setConnStatus(true);
        log('WS CONNECTED', 'success');
        ws.send(JSON.stringify({ action: 'status' }));
    };

    ws.onmessage = (evt) => {
        try {
            const msg = JSON.parse(evt.data);
            handleWsMessage(msg);
        } catch (e) {
            console.error('WS parse error', e);
        }
    };

    ws.onclose = () => {
        setConnStatus(false);
        log('WS DISCONNECTED — reconnecting...', 'error');
        setTimeout(initWebSocket, 3000);
    };

    ws.onerror = () => {
        setConnStatus(false);
    };
}

function handleWsMessage(msg) {
    if (msg.type === 'agents') {
        renderAgents(msg.data);
    } else if (msg.event) {
        const evtType = msg.error ? 'error' : (msg.completed ? 'success' : 'active');
        log(`[${msg.event.toUpperCase()}] node=${msg.node || '-'} run=${msg.run_id?.slice(0, 6) || '-'}`, evtType);
        if (msg.completed) loadRuns();
    }
}

function setConnStatus(connected) {
    const el = document.getElementById('conn-status');
    if (!el) return;
    if (connected) {
        el.innerHTML = '<span class="dot dot-green"></span> WS CONNECTED';
    } else {
        el.innerHTML = '<span class="dot dot-red"></span> WS DISCONNECTED';
    }
}

// ═══════════════════════════════════════════════════════════════
//  AGENTS
// ═══════════════════════════════════════════════════════════════

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
        container.innerHTML = '<div class="agent-card ghost"><div class="agent-info">No agents registered</div></div>';
        return;
    }

    const initials = name => name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();

    container.innerHTML = agents.map(a => {
        const statusClass = `status-${a.status || 'idle'}`;
        return `
        <div class="agent-card">
            <div class="agent-avatar">${initials(a.name)}</div>
            <div class="agent-info">
                <div class="agent-name">${a.name}</div>
                <div class="agent-desc">${a.description || 'Multi-purpose agent node'}</div>
                <div class="agent-stats">
                    <span class="agent-stat">Exec: <span class="agent-stat-value">${a.executions || 0}</span></span>
                    <span class="agent-stat">Fail: <span class="agent-stat-value">${a.failures || 0}</span></span>
                    <span class="agent-stat">Model: <span class="agent-stat-value">${a.model || 'N/A'}</span></span>
                </div>
            </div>
            <span class="status-badge ${statusClass}">
                <span class="status-dot"></span>
                ${a.status || 'idle'}
            </span>
        </div>
        `;
    }).join('');
}

// ═══════════════════════════════════════════════════════════════
//  TASK SUBMIT
// ═══════════════════════════════════════════════════════════════

async function submitTask() {
    const title = document.getElementById('task-title').value.trim();
    const desc = document.getElementById('task-desc').value.trim();
    const priority = document.getElementById('task-priority').value;
    const btn = document.getElementById('btn-submit');

    if (!title || !desc) {
        log('ERROR: Title and description required', 'error');
        return;
    }

    btn.disabled = true;
    log(`DISPATCHING: ${title}`, 'active');

    try {
        const res = await fetch(`${API_BASE}/api/tasks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, description: desc, priority })
        });
        const data = await res.json();

        if (data.run_id) {
            log(`LAUNCHED: run_id=${data.run_id}`, 'success');
            document.getElementById('task-title').value = '';
            document.getElementById('task-desc').value = '';
            openRunStream(data.run_id);
            loadRuns();
        } else {
            log(`LAUNCH FAILED: ${data.message || 'unknown'}`, 'error');
        }
    } catch (e) {
        log(`NETWORK ERROR: ${e.message}`, 'error');
    } finally {
        btn.disabled = false;
    }
}

// ═══════════════════════════════════════════════════════════════
//  RUNS
// ═══════════════════════════════════════════════════════════════

async function loadRuns() {
    // We don't have a direct /api/runs endpoint, but we can query traces
    // For now, update the UI with any runs we know about from SSE/WS
    renderRuns();
}

function addRun(run) {
    const existing = runs.find(r => r.run_id === run.run_id);
    if (existing) {
        Object.assign(existing, run);
    } else {
        runs.unshift(run);
        if (runs.length > 20) runs.pop();
    }
    renderRuns();
}

function renderRuns() {
    const container = document.getElementById('runs-list');
    if (!runs.length) {
        container.innerHTML = '<div class="run-empty">Awaiting mission dispatch...</div>';
        return;
    }

    container.innerHTML = runs.map(r => {
        const status = r.status || 'PENDING';
        return `
        <div class="run-item" data-run-id="${r.run_id}">
            <div class="run-info">
                <div class="run-title">${r.title || 'Untitled Mission'}</div>
                <div class="run-meta">${r.run_id} // ${r.created_at ? formatTime(r.created_at) : ''}</div>
            </div>
            <span class="run-status ${status}">${status}</span>
        </div>
        `;
    }).join('');
}

function formatTime(iso) {
    try {
        const d = new Date(iso);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
        return '';
    }
}

// ═══════════════════════════════════════════════════════════════
//  SSE STREAM
// ═══════════════════════════════════════════════════════════════

function openRunStream(runId) {
    const evtSource = new EventSource(`${API_BASE}/api/tasks/${runId}/stream`);

    evtSource.onmessage = (e) => {
        try {
            const data = JSON.parse(e.data);
            const evtType = data.completed ? 'success' : (data.error ? 'error' : 'active');
            log(`[STREAM ${runId.slice(0, 6)}] ${data.event} | node=${data.node || '-'}`, evtType);

            // Update runs list
            addRun({
                run_id: runId,
                title: data.title || 'Mission',
                status: data.completed ? 'COMPLETED' : (data.error ? 'ERROR' : 'RUNNING'),
                created_at: new Date().toISOString()
            });

            if (data.completed || data.event === 'timeout') {
                evtSource.close();
            }
        } catch (err) {
            console.error('SSE parse error', err);
        }
    };

    evtSource.onerror = () => {
        evtSource.close();
    };
}

// ═══════════════════════════════════════════════════════════════
//  LOGGER (Telemetry Feed)
// ═══════════════════════════════════════════════════════════════

function log(text, type = 'active') {
    const el = document.getElementById('live-log');
    if (!el) return;

    const entry = document.createElement('div');
    entry.className = `log-entry ${type}`;

    const ts = new Date().toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    entry.innerHTML = `<span class="log-timestamp">[${ts}]</span>${text}`;

    el.appendChild(entry);
    el.scrollTop = el.scrollHeight;

    // Auto-remove old entries to prevent DOM bloat
    while (el.children.length > 100) {
        el.removeChild(el.firstChild);
    }

    // Fade out "active" entries after a moment
    if (type === 'active') {
        setTimeout(() => entry.classList.remove('active'), 2000);
    }
}
