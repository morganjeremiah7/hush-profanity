(async function () {
    const ul = document.getElementById('library');
    const filter = document.getElementById('filter');
    const items = await fetch('/api/library').then(r => r.json());

    function render(query) {
        const q = (query || '').toLowerCase();
        ul.innerHTML = '';
        for (const it of items) {
            if (q && !it.rel.toLowerCase().includes(q) && !it.name.toLowerCase().includes(q)) continue;
            const li = document.createElement('li');
            const a = document.createElement('a');
            a.href = `/watch?path=${encodeURIComponent(it.path)}`;
            const left = document.createElement('span');
            left.innerHTML = `<span class="name">${escape(it.name)}</span><br><span class="rel">${escape(it.rel)}</span>`;
            const right = document.createElement('span');
            if (it.has_edl) right.innerHTML = '<span class="badge">EDL</span>';
            a.appendChild(left); a.appendChild(right);
            li.appendChild(a);
            ul.appendChild(li);
        }
        if (!ul.children.length) {
            ul.innerHTML = '<li class="muted" style="padding:1rem;text-align:center">no matches</li>';
        }
    }
    function escape(s) {
        return s.replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' })[c]);
    }
    filter.addEventListener('input', () => render(filter.value));
    render('');
})();
