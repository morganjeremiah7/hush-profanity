(function () {
    "use strict";

    const form = document.getElementById("settings-form");
    const formStatus = document.getElementById("form-status");
    const formErrors = document.getElementById("form-errors");
    const rootsEl = document.getElementById("library__roots");
    const rootsStatus = document.getElementById("roots-status");

    let editableKeys = {};
    let allowedValues = {};

    async function loadSettings() {
        formStatus.textContent = "loading...";
        const res = await fetch("/api/settings");
        if (!res.ok) {
            formStatus.textContent = "load failed";
            return;
        }
        const j = await res.json();
        editableKeys = j.editable_keys || {};
        allowedValues = j.allowed_values || {};
        populateDropdowns();
        applyData(j.data || {});
        formStatus.textContent = j.exists ? "" : "no settings.toml yet — defaults loaded from example";
        checkRoots();
    }

    function populateDropdowns() {
        const modelSel = document.getElementById("whisper__model");
        const ctSel = document.getElementById("whisper__compute_type");
        modelSel.innerHTML = "";
        for (const m of allowedValues["whisper.model"] || []) {
            const opt = document.createElement("option");
            opt.value = m;
            opt.textContent = m;
            modelSel.appendChild(opt);
        }
        ctSel.innerHTML = "";
        for (const c of allowedValues["whisper.compute_type"] || []) {
            const opt = document.createElement("option");
            opt.value = c;
            opt.textContent = c;
            ctSel.appendChild(opt);
        }
    }

    function applyData(data) {
        // Library
        const lib = data.library || {};
        rootsEl.value = (lib.roots || []).join("\n");
        document.getElementById("library__extensions").value = (lib.extensions || []).join("\n");
        document.getElementById("library__skip_if_processed").checked = !!lib.skip_if_processed;

        const wh = data.whisper || {};
        setSelect("whisper__model", wh.model);
        setSelect("whisper__compute_type", wh.compute_type);
        document.getElementById("whisper__audio_language").value = wh.audio_language || "";

        const al = data.alignment || {};
        document.getElementById("alignment__enabled").checked = !!al.enabled;

        const ed = data.edl || {};
        setSelect("edl__profanity_action", String(ed.profanity_action ?? 1));
        document.getElementById("edl__padding_seconds").value = ed.padding_seconds ?? 0.1;
        document.getElementById("edl__merge_gap_seconds").value = ed.merge_gap_seconds ?? 2.0;

        const pf = data.performance || {};
        document.getElementById("performance__gpu_workers").value = pf.gpu_workers ?? 1;

        const wu = data.webui || {};
        document.getElementById("webui__port").value = wu.port ?? 8765;
        setSelect("webui__default_action", String(wu.default_action ?? 0));
    }

    function setSelect(id, value) {
        const el = document.getElementById(id);
        if (!el) return;
        const v = String(value);
        for (const opt of el.options) {
            if (opt.value === v) { opt.selected = true; return; }
        }
    }

    function collectUpdates() {
        const lines = (s) => s.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
        return {
            library: {
                roots: lines(rootsEl.value),
                extensions: lines(document.getElementById("library__extensions").value)
                    .map(x => x.startsWith(".") ? x.toLowerCase() : "." + x.toLowerCase()),
                skip_if_processed: document.getElementById("library__skip_if_processed").checked,
            },
            whisper: {
                model: document.getElementById("whisper__model").value,
                compute_type: document.getElementById("whisper__compute_type").value,
                audio_language: document.getElementById("whisper__audio_language").value.trim().toLowerCase(),
            },
            alignment: {
                enabled: document.getElementById("alignment__enabled").checked,
            },
            edl: {
                profanity_action: parseInt(document.getElementById("edl__profanity_action").value, 10),
                padding_seconds: parseFloat(document.getElementById("edl__padding_seconds").value),
                merge_gap_seconds: parseFloat(document.getElementById("edl__merge_gap_seconds").value),
            },
            performance: {
                gpu_workers: parseInt(document.getElementById("performance__gpu_workers").value, 10),
            },
            webui: {
                port: parseInt(document.getElementById("webui__port").value, 10),
                default_action: parseInt(document.getElementById("webui__default_action").value, 10),
            },
        };
    }

    async function checkRoots() {
        const paths = rootsEl.value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
        if (!paths.length) {
            rootsStatus.innerHTML = "<span class='warn'>⚠ No roots set — the library page will be empty until at least one is added.</span>";
            return;
        }
        try {
            const res = await fetch("/api/check-paths", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({paths}),
            });
            const j = await res.json();
            const rows = paths.map(p => {
                const r = j[p] || {exists: false};
                if (r.exists && r.is_dir) return `<span class='ok'>✓ ${escape(p)}</span>`;
                if (r.exists) return `<span class='warn'>⚠ ${escape(p)} exists but is not a directory</span>`;
                return `<span class='bad'>✗ ${escape(p)} — not reachable from this machine</span>`;
            });
            rootsStatus.innerHTML = rows.join("<br>");
        } catch (e) {
            rootsStatus.textContent = "(could not validate paths)";
        }
    }

    function escape(s) {
        return String(s).replace(/[&<>"']/g, c => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
    }

    async function save(e) {
        e.preventDefault();
        formErrors.hidden = true;
        formErrors.innerHTML = "";
        formStatus.textContent = "saving...";
        const updates = collectUpdates();
        const res = await fetch("/api/settings", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({updates}),
        });
        const j = await res.json();
        if (!res.ok || !j.ok) {
            const errs = (j.errors || ["save failed"]);
            formErrors.innerHTML = "<strong>Save failed:</strong><ul>"
                + errs.map(e => "<li>" + escape(e) + "</li>").join("") + "</ul>";
            formErrors.hidden = false;
            formStatus.textContent = "";
            return;
        }
        formStatus.textContent = j.note || "saved";
        // Re-validate root paths after save
        checkRoots();
    }

    form.addEventListener("submit", save);
    document.getElementById("reload-btn").addEventListener("click", loadSettings);
    rootsEl.addEventListener("blur", checkRoots);

    loadSettings();
})();
