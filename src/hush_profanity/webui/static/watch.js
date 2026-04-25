(function () {
    const videoPath = document.body.dataset.videoPath;
    const defaultAction = parseInt(document.body.dataset.defaultAction || '0', 10);

    const player = document.getElementById('player');
    const nowEl = document.getElementById('now');
    const inEl = document.getElementById('mark-in');
    const outEl = document.getElementById('mark-out');
    const actionSel = document.getElementById('action');
    const manualBody = document.querySelector('#manual tbody');
    const autoBody = document.querySelector('#auto tbody');
    const manualCount = document.getElementById('manual-count');
    const autoCount = document.getElementById('auto-count');

    actionSel.value = String(defaultAction);

    let markIn = null;
    let markOut = null;
    let manual = []; // {start,end,action,comment}
    let autoEntries = [];
    let dirty = false;

    function fmt(t) {
        if (t == null || isNaN(t)) return '—';
        const ms = Math.round((t % 1) * 1000);
        const s = Math.floor(t) % 60;
        const m = Math.floor(t / 60) % 60;
        const h = Math.floor(t / 3600);
        return `${pad(h)}:${pad(m)}:${pad(s)}.${String(ms).padStart(3,'0')}`;
    }
    function pad(n) { return String(n).padStart(2,'0'); }

    function tick() {
        nowEl.textContent = fmt(player.currentTime);
        requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);

    function refreshTables() {
        manualBody.innerHTML = '';
        manual.forEach((e, i) => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><a href="#" data-seek="${e.start}">${fmt(e.start)}</a></td>
                <td><a href="#" data-seek="${e.end}">${fmt(e.end)}</a></td>
                <td>${(e.end - e.start).toFixed(2)}s</td>
                <td>${e.action === 0 ? 'cut' : 'mute'}</td>
                <td><input type="text" data-i="${i}" data-field="comment" value="${(e.comment||'').replace(/"/g,'&quot;')}" placeholder="(optional note)"></td>
                <td><button class="row-del" data-i="${i}" title="delete">×</button></td>`;
            manualBody.appendChild(tr);
        });
        manualCount.textContent = `(${manual.length})`;

        autoBody.innerHTML = '';
        autoEntries.forEach(e => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><a href="#" data-seek="${e.start}">${fmt(e.start)}</a></td>
                <td><a href="#" data-seek="${e.end}">${fmt(e.end)}</a></td>
                <td>${e.action === 0 ? 'cut' : (e.action === 1 ? 'mute' : e.action)}</td>
                <td>${escape(e.comment || '')}</td>`;
            autoBody.appendChild(tr);
        });
        autoCount.textContent = `(${autoEntries.length})`;
    }
    function escape(s) {
        return String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' })[c]);
    }
    function toast(msg, kind='ok') {
        const t = document.createElement('div');
        t.className = `toast ${kind}`;
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 2500);
    }

    document.body.addEventListener('click', (e) => {
        const seek = e.target.closest('[data-seek]');
        if (seek) {
            e.preventDefault();
            player.currentTime = parseFloat(seek.dataset.seek);
            return;
        }
        const del = e.target.closest('.row-del');
        if (del) {
            const i = parseInt(del.dataset.i, 10);
            manual.splice(i, 1);
            dirty = true;
            refreshTables();
        }
    });
    document.body.addEventListener('input', (e) => {
        if (e.target.matches('input[data-field="comment"]')) {
            const i = parseInt(e.target.dataset.i, 10);
            manual[i].comment = e.target.value;
            dirty = true;
        }
    });

    document.getElementById('btn-in').addEventListener('click', setIn);
    document.getElementById('btn-out').addEventListener('click', setOut);
    document.getElementById('btn-add').addEventListener('click', addEntry);
    document.getElementById('btn-clear').addEventListener('click', clearMarks);
    document.getElementById('btn-save').addEventListener('click', save);

    function setIn() { markIn = player.currentTime; inEl.textContent = fmt(markIn); }
    function setOut() { markOut = player.currentTime; outEl.textContent = fmt(markOut); }
    function clearMarks() { markIn = markOut = null; inEl.textContent = outEl.textContent = '—'; }
    function addEntry() {
        if (markIn == null || markOut == null) {
            toast('set both IN and OUT first', 'error');
            return;
        }
        const lo = Math.min(markIn, markOut);
        const hi = Math.max(markIn, markOut);
        if (hi - lo < 0.05) {
            toast('range too short', 'error');
            return;
        }
        manual.push({ start: lo, end: hi, action: parseInt(actionSel.value, 10), comment: '' });
        manual.sort((a, b) => a.start - b.start);
        dirty = true;
        clearMarks();
        refreshTables();
    }

    async function save() {
        try {
            const r = await fetch(`/api/edl?path=${encodeURIComponent(videoPath)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ manual }),
            });
            if (!r.ok) throw new Error(await r.text());
            const j = await r.json();
            dirty = false;
            toast(`saved ${j.manual_count} entr${j.manual_count===1?'y':'ies'}`, 'ok');
        } catch (e) {
            toast(`save failed: ${e.message}`, 'error');
        }
    }

    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        switch (e.key.toLowerCase()) {
            case ' ': e.preventDefault(); player.paused ? player.play() : player.pause(); break;
            case 'j': player.currentTime = Math.max(0, player.currentTime - 5); break;
            case 'l': player.currentTime = Math.min(player.duration || 1e9, player.currentTime + 5); break;
            case ',': player.currentTime = Math.max(0, player.currentTime - 0.1); break;
            case '.': player.currentTime = Math.min(player.duration || 1e9, player.currentTime + 0.1); break;
            case 'i': setIn(); break;
            case 'o': setOut(); break;
            case 'enter': addEntry(); break;
            case 'escape': clearMarks(); break;
        }
    });

    window.addEventListener('beforeunload', (e) => {
        if (dirty) { e.preventDefault(); e.returnValue = ''; }
    });

    fetch(`/api/edl?path=${encodeURIComponent(videoPath)}`)
        .then(r => r.json())
        .then(d => {
            manual = d.manual || [];
            autoEntries = d.auto || [];
            refreshTables();
        });
})();
