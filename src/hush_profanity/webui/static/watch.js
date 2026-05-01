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
    const tuneBanner = document.getElementById('tune-banner');
    const tuneWhich = document.getElementById('tune-which');
    const tuneTime = document.getElementById('tune-time');

    actionSel.value = String(defaultAction);

    // Approximate one-frame step. HTML5 video gives no reliable framerate
    // readout, so we treat ~30 fps (≈33 ms) as the universal nudge unit.
    // Most content is 24/25/30 fps; for our purposes (mute regions consumed
    // by Kodi which seeks to keyframes anyway), single-frame precision in
    // either direction is well within tolerance.
    const FRAME_S = 1 / 30;

    let markIn = null;
    let markOut = null;
    let manual = []; // {start,end,action,comment}
    let autoEntries = [];
    let dirty = false;
    // null | 'in' | 'out' — when set, ←/→ nudge the just-placed marker
    // instead of doing a normal 10s seek.
    let tuneTarget = null;

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
    document.getElementById('btn-playpause').addEventListener('click', togglePlay);
    document.querySelectorAll('.transport [data-seek-by]').forEach(btn => {
        btn.addEventListener('click', () => {
            if (tuneTarget) exitTune(false);   // user is jumping away — drop the tune
            seekBy(parseFloat(btn.dataset.seekBy));
        });
    });

    function clamp(t) {
        const max = player.duration || 1e9;
        return Math.max(0, Math.min(max, t));
    }
    function seekBy(delta) {
        player.currentTime = clamp((player.currentTime || 0) + delta);
    }
    function togglePlay() {
        if (tuneTarget) exitTune(true);   // confirm + play
        else if (player.paused) player.play();
        else player.pause();
    }

    function enterTune(which) {
        tuneTarget = which;
        player.pause();
        const t = which === 'in' ? markIn : markOut;
        player.currentTime = clamp(t);
        tuneWhich.textContent = which.toUpperCase();
        tuneTime.textContent = fmt(t);
        tuneBanner.hidden = false;
    }
    function exitTune(play) {
        tuneTarget = null;
        tuneBanner.hidden = true;
        if (play) player.play();
    }
    function nudgeMarker(deltaSeconds) {
        if (!tuneTarget) return;
        const updated = clamp((tuneTarget === 'in' ? markIn : markOut) + deltaSeconds);
        if (tuneTarget === 'in')  { markIn  = updated; inEl.textContent  = fmt(updated); }
        else                      { markOut = updated; outEl.textContent = fmt(updated); }
        player.currentTime = updated;
        tuneTime.textContent = fmt(updated);
    }

    function setIn() {
        markIn = player.currentTime;
        inEl.textContent = fmt(markIn);
        toast(`IN set @ ${fmt(markIn)} — adjust with ←/→, Space to confirm`, 'ok');
        enterTune('in');
    }
    function setOut() {
        markOut = player.currentTime;
        outEl.textContent = fmt(markOut);
        toast(`OUT set @ ${fmt(markOut)} — adjust with ←/→, Space to confirm`, 'ok');
        enterTune('out');
    }
    function clearMarks() {
        markIn = markOut = null;
        inEl.textContent = outEl.textContent = '—';
        if (tuneTarget) exitTune(false);
    }
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
        // Don't swallow keys while typing in a comment field.
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

        // Arrow-key behavior depends on whether a marker is being fine-tuned.
        // In tune mode: nudge the marker by 1 frame (or 10 with Shift).
        // Out of tune mode: 10 s seek (30 s with Shift) — streaming convention.
        if (e.key === 'ArrowLeft') {
            e.preventDefault();
            if (tuneTarget) nudgeMarker(e.shiftKey ? -10 * FRAME_S : -FRAME_S);
            else            seekBy(e.shiftKey ? -30 : -10);
            return;
        }
        if (e.key === 'ArrowRight') {
            e.preventDefault();
            if (tuneTarget) nudgeMarker(e.shiftKey ?  10 * FRAME_S :  FRAME_S);
            else            seekBy(e.shiftKey ?  30 :  10);
            return;
        }
        switch (e.key.toLowerCase()) {
            case ' ': e.preventDefault(); togglePlay(); break;
            case 'j': seekBy(e.shiftKey ? -30 : -5); break;
            case 'l': seekBy(e.shiftKey ?  30 :  5); break;
            case ',': if (tuneTarget) nudgeMarker(-0.1); else seekBy(-0.1); break;
            case '.': if (tuneTarget) nudgeMarker( 0.1); else seekBy( 0.1); break;
            case 'i': setIn(); break;
            case 'o': setOut(); break;
            case 'enter': addEntry(); break;
            case 'escape': if (tuneTarget) exitTune(false); else clearMarks(); break;
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
