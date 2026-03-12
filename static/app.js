// ═══════════════════════════════════════════════════════════
// StreamForge v3.0 — Pro-Grade Frontend + WebSocket Progress
// ═══════════════════════════════════════════════════════════

const API = '';
let currentVideoId = null;
let currentJobId = null;
let selectedQualities = new Set();
let pollInterval = null;
let elapsedTimer = null;
let qualityAnalysis = null;
let hardwareInfo = null;
let videoInfo = null;
let selectedPreset = 'balanced';
let currentEstimate = null;
let processStartTime = null;
let wsConnection = null;  // WebSocket connection

const $ = id => document.getElementById(id);

const dropZone = $('drop-zone');
const fileInput = $('file-input');
const stepUpload = $('step-upload');
const stepSettings = $('step-settings');
const stepProcessing = $('step-processing');
const stepResult = $('step-result');
const btnProcess = $('btn-process');
const btnNew = $('btn-new');
const btnCopy = $('btn-copy');
const btnDownload = $('btn-download');
const btnCancel = $('btn-cancel');

// ═══════ HELPERS ═══════
function fmtTime(s) {
    s = Math.round(s);
    if (s >= 3600) return `${Math.floor(s/3600)}:${String(Math.floor((s%3600)/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
    return `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
}
function fmtDur(s) { return `${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,'0')}`; }

// ═══════ NAVIGATION ═══════
function showStep(step) {
    [stepUpload, stepSettings, stepProcessing, stepResult].forEach(s => s.classList.add('hidden'));
    step.classList.remove('hidden');
    step.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ═══════ HARDWARE ═══════
async function detectHardware() {
    try {
        const res = await fetch(`${API}/api/hardware`);
        hardwareInfo = await res.json();
        renderHardwareInfo();
    } catch (err) {
        $('hw-loading').innerHTML = '<span style="color:var(--danger)">Hardware detection failed</span>';
    }
}

function renderHardwareInfo() {
    const hw = hardwareInfo;
    $('hw-loading').style.display = 'none';
    $('hw-info').classList.remove('hidden');

    const badge = $('hw-mode-badge');
    badge.textContent = hw.best_mode === 'gpu' ? 'GPU' : 'CPU';
    badge.className = `hw-mode-badge ${hw.best_mode}`;

    $('hw-cpu-name').textContent = hw.cpu_name;
    $('hw-cpu-cores').textContent = `${hw.cpu_cores} core / ${hw.cpu_threads} thread`;
    if (hw.gpu_name) {
        $('hw-gpu-name').textContent = hw.gpu_name;
        $('hw-gpu-status').textContent = hw.gpu_available ? 'Encoder available' : 'Not found';
    } else {
        $('hw-gpu-name').textContent = 'Not detected';
        $('hw-gpu-status').textContent = 'No GPU';
    }

    const sel = $('hw-encoder');
    sel.innerHTML = '<option value="libx264">CPU — libx264</option>';
    hw.gpu_encoders.filter(e => e.available).forEach(enc => {
        const opt = document.createElement('option');
        opt.value = enc.name; opt.textContent = `${enc.vendor} — ${enc.label}`;
        sel.appendChild(opt);
    });
    if (hw.best_mode === 'gpu') sel.value = hw.best_encoder;
    $('hw-threads').max = hw.cpu_threads;

    sel.addEventListener('change', () => {
        $('threads-control').style.display = sel.value !== 'libx264' ? 'none' : '';
        fetchEstimate();
    });
}

// ═══════ PRESETS ═══════
async function loadPresets() {
    try {
        const res = await fetch(`${API}/api/presets`);
        const presets = await res.json();
        renderPresets(presets);
    } catch (err) {}
}

function renderPresets(presets) {
    const grid = $('preset-options');
    grid.innerHTML = '';
    Object.entries(presets).forEach(([key, p]) => {
        const card = document.createElement('div');
        card.className = `preset-card${key === selectedPreset ? ' selected' : ''}`;
        card.innerHTML = `<div class="preset-icon">${p.icon}</div><div class="preset-body">
            <span class="preset-name">${p.label}</span><span class="preset-desc">${p.description}</span></div>`;
        card.addEventListener('click', () => {
            document.querySelectorAll('.preset-card').forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            selectedPreset = key;
            fetchEstimate();
        });
        grid.appendChild(card);
    });
}

// ═══════ UI CONTROLS ═══════
$('hw-threads')?.addEventListener('input', (e) => {
    $('threads-value').textContent = e.target.value === '0' ? 'Auto' : e.target.value;
    fetchEstimate();
});
$('hw-parallel')?.addEventListener('change', (e) => {
    $('parallel-control').classList.toggle('hidden', !e.target.checked);
    fetchEstimate();
});
$('hw-max-parallel')?.addEventListener('input', (e) => { $('parallel-value').textContent = e.target.value; fetchEstimate(); });
document.querySelectorAll('.radio-card').forEach(card => {
    card.addEventListener('click', () => { document.querySelectorAll('.radio-card').forEach(c => c.classList.remove('active')); card.classList.add('active'); });
});
['trim-start', 'trim-end'].forEach(id => $(id)?.addEventListener('input', () => { updateTrimInfo(); fetchEstimate(); }));

function updateTrimInfo() {
    if (!videoInfo) return;
    const start = parseFloat($('trim-start').value) || 0;
    const end = parseFloat($('trim-end').value) || videoInfo.duration;
    const info = $('trim-info');
    if (start === 0 && (end === 0 || end >= videoInfo.duration)) {
        info.textContent = "Full video"; info.style.color = 'var(--text-secondary)';
    } else {
        info.textContent = `Trimmed: ${(end - start).toFixed(1)}s (${fmtDur(start)} — ${fmtDur(end)})`;
        info.style.color = 'var(--accent-light)';
    }
}

detectHardware();
loadPresets();

// ═══════ UPLOAD ═══════
dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', e => { e.preventDefault(); dropZone.classList.remove('dragover'); if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]); });
fileInput.addEventListener('change', e => { if (e.target.files[0]) uploadFile(e.target.files[0]); });

async function uploadFile(file) {
    document.querySelector('.drop-zone-content').style.display = 'none';
    $('upload-progress').classList.remove('hidden');
    const formData = new FormData(); formData.append('file', file);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API}/api/upload`);
    xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
            const p = Math.round((e.loaded / e.total) * 100);
            $('upload-fill').style.width = `${p}%`;
            $('upload-status').textContent = `Uploading... ${p}%`;
        }
    };
    xhr.onload = () => {
        if (xhr.status === 200) {
            const data = JSON.parse(xhr.responseText);
            currentVideoId = data.video_id;
            $('upload-fill').style.width = '100%'; $('upload-status').textContent = 'Uploaded!';
            setTimeout(() => { showVideoInfo(data); showStep(stepSettings); }, 400);
        } else { $('upload-status').textContent = 'Error!'; $('upload-fill').style.background = 'var(--danger)'; }
    };
    xhr.onerror = () => { $('upload-status').textContent = 'Cannot connect to server!'; };
    xhr.send(formData);
}

// ═══════ VIDEO INFO ═══════
function showVideoInfo(data) {
    const info = data.info; videoInfo = info; qualityAnalysis = info.quality_analysis;
    const files = fileInput.files;
    if (files && files[0]) $('preview-video').src = URL.createObjectURL(files[0]);
    $('video-filename').textContent = data.filename;
    $('meta-resolution').textContent = `${info.width}x${info.height}`;
    $('meta-duration').textContent = info.duration_formatted;
    $('meta-size').textContent = `${info.size_mb} MB`;
    $('meta-fps').textContent = `${info.fps} fps`;
    $('meta-codec').textContent = info.video_codec.toUpperCase();
    $('meta-audio').textContent = info.has_audio ? `${info.audio_codec.toUpperCase()} ${info.audio_channels || ''}ch` : "None";
    $('trim-end').value = info.duration.toFixed(1);
    $('trim-end').max = info.duration; $('trim-start').max = info.duration;

    if (qualityAnalysis) {
        const qa = qualityAnalysis; const banner = $('quality-analysis');
        banner.classList.remove('hidden'); banner.className = `quality-analysis tier-${qa.category.tier}`;
        $('qa-icon').textContent = qa.category.icon;
        $('qa-category').textContent = qa.category.name;
        $('qa-original').textContent = `Original: ${qa.original.label} | ${info.video_codec.toUpperCase()} | ${info.bitrate_formatted}${info.is_hdr ? ' | HDR' : ''}`;
        $('qa-max').textContent = qa.max_quality || '\u2014';
        $('qa-min').textContent = qa.min_quality || '\u2014';
        $('qa-ratio').textContent = qa.aspect_ratio;
        $('qa-orient').textContent = { landscape: 'Landscape', portrait: 'Portrait', square: 'Square' }[qa.orientation] || qa.orientation;
        const est = qa.estimated_output;
        $('qa-size').textContent = est.estimated_size_mb > 1024 ? `~${(est.estimated_size_mb / 1024).toFixed(1)} GB` : `~${Math.round(est.estimated_size_mb)} MB`;
        $('qa-count').textContent = `${qa.recommended_qualities.length} total`;
        $('qa-recommend-text').innerHTML = `Recommended: <strong>${qa.recommended_qualities.join(', ')}</strong>`;
    }

    // Quality grid
    const grid = $('quality-options'); grid.innerHTML = ''; selectedQualities.clear();
    const recommended = qualityAnalysis ? qualityAnalysis.recommended_qualities : [];
    const available = qualityAnalysis ? qualityAnalysis.all_quality_names : info.available_qualities;
    ['4K', '2K', '1080p', '720p', '480p', '360p', '240p', '144p'].forEach(q => {
        const isAvail = available.includes(q), isRec = recommended.includes(q);
        const card = document.createElement('label');
        card.className = `quality-card${!isAvail ? ' disabled' : ''}${isRec ? ' selected' : ''}`;
        card.innerHTML = `<input type="checkbox" value="${q}" ${isRec ? 'checked' : ''} ${!isAvail ? 'disabled' : ''}>
            <span class="quality-check">${isRec ? '\u2713' : ''}</span>
            <span class="quality-label">${q}${isRec ? ' \u2b50' : ''}</span>`;
        if (isRec) selectedQualities.add(q);
        if (isAvail) card.addEventListener('click', e => {
            e.preventDefault(); const cb = card.querySelector('input'); cb.checked = !cb.checked;
            card.classList.toggle('selected', cb.checked);
            card.querySelector('.quality-check').textContent = cb.checked ? '\u2713' : '';
            cb.checked ? selectedQualities.add(q) : selectedQualities.delete(q);
            btnProcess.disabled = selectedQualities.size === 0;
            fetchEstimate();
        });
        grid.appendChild(card);
    });
    btnProcess.disabled = selectedQualities.size === 0;
    fetchEstimate();
}

// ═══════ ESTIMATE ═══════
let estimateTimeout = null;
function fetchEstimate() {
    if (!currentVideoId || selectedQualities.size === 0) {
        $('estimate-panel')?.classList.add('hidden');
        return;
    }
    clearTimeout(estimateTimeout);
    estimateTimeout = setTimeout(async () => {
        try {
            const res = await fetch(`${API}/api/estimate`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(getSettings()),
            });
            if (res.ok) {
                currentEstimate = await res.json();
                renderEstimate(currentEstimate);
            }
        } catch (err) {
            console.warn('Estimate error:', err);
        }
    }, 300);
}

function getSettings() {
    // Codec selector overrides encoder if using CPU
    const codecSel = $('pro-codec')?.value;
    const hwEncoder = $('hw-encoder')?.value || 'libx264';
    // If GPU encoder selected, use it; otherwise use codec selector
    const encoder = (hwEncoder !== 'libx264' && hwEncoder !== 'libx265') ? hwEncoder : (codecSel || 'libx264');

    return {
        video_id: currentVideoId,
        qualities: Array.from(selectedQualities),
        encoding_preset: selectedPreset,
        encoder: encoder,
        parallel: $('hw-parallel')?.checked || false,
        max_parallel: parseInt($('hw-max-parallel')?.value || '2'),
        trim_start: parseFloat($('trim-start')?.value) || 0,
        trim_end: parseFloat($('trim-end')?.value) || 0,
        threads: parseInt($('hw-threads')?.value || '0'),
        // Pro features
        encrypt: $('pro-encrypt')?.checked || false,
        extract_subs: $('pro-subtitles')?.checked ?? true,
        generate_sprites: $('pro-sprites')?.checked ?? true,
    };
}

function renderEstimate(est) {
    const panel = $('estimate-panel');
    if (!panel) return;
    panel.classList.remove('hidden');

    $('est-time').textContent = est.total_estimated_time_formatted;
    $('est-size').textContent = est.total_output_size_formatted;
    $('est-compress').textContent = `${est.compression_ratio}x`;
    $('est-mode').textContent = `${est.hw_mode} | ${est.preset_label} | ${est.processing_mode}`;

    const tbody = $('est-qualities');
    tbody.innerHTML = '';
    est.qualities.forEach(q => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${q.name}</td><td>${q.bitrate}</td>
            <td><strong>${q.estimated_time_formatted}</strong></td>
            <td>${q.estimated_size_mb} MB</td>`;
        tbody.appendChild(tr);
    });

    btnProcess.innerHTML = `<span class="btn-icon">&#128640;</span>
        <span>Start Processing \u2014 ${est.total_estimated_time_formatted} | ${est.total_output_size_formatted}</span>`;
}

// ═══════ PROCESS ═══════
btnProcess.addEventListener('click', startProcessing);
btnCancel.addEventListener('click', cancelProcessing);

async function startProcessing() {
    const body = { ...getSettings(),
        segment_duration: parseInt(document.querySelector('input[name="segment"]:checked')?.value || '4'),
        generate_thumbnail: $('gen-thumbnail')?.checked ?? true,
        audio_bitrate: $('audio-bitrate')?.value || '',
        audio_normalize: $('audio-normalize')?.checked || false,
    };

    showStep(stepProcessing);
    setupProcessSteps(body);
    processStartTime = Date.now();
    btnCancel.classList.remove('hidden'); // Show cancel button

    // Show estimate from settings page (client-side fallback)
    if (currentEstimate) {
        $('proc-est-time').textContent = currentEstimate.total_estimated_time_formatted;
        $('proc-est-size').textContent = currentEstimate.total_output_size_formatted;
        $('proc-eta').textContent = currentEstimate.total_estimated_time_formatted;
    }

    // Start client-side elapsed timer
    if (elapsedTimer) clearInterval(elapsedTimer);
    elapsedTimer = setInterval(() => {
        const elapsed = (Date.now() - processStartTime) / 1000;
        $('proc-elapsed').textContent = fmtTime(elapsed);
    }, 500);

    try {
        const res = await fetch(`${API}/api/process`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        currentJobId = data.job_id;

        // Override with server estimate if available
        if (data.estimate) {
            $('proc-est-time').textContent = data.estimate.total_estimated_time_formatted;
            $('proc-est-size').textContent = data.estimate.total_output_size_formatted;
            $('proc-eta').textContent = data.estimate.total_estimated_time_formatted;
        }

        // Try WebSocket first, fall back to polling
        connectWebSocket(data.job_id);
    } catch (err) {
        $('process-status').textContent = `Error: ${err.message}`;
        clearInterval(elapsedTimer);
        btnCancel.classList.add('hidden');
    }
}

async function cancelProcessing() {
    if (!currentJobId) return;

    btnCancel.disabled = true;
    btnCancel.textContent = 'Cancelling...';

    try {
        const res = await fetch(`${API}/api/cancel/${currentJobId}`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            $('process-status').textContent = 'Processing cancelled by user.';
            clearInterval(elapsedTimer);
            if (pollInterval) clearInterval(pollInterval);
            if (wsConnection) wsConnection.close();
            btnCancel.classList.add('hidden');
        } else {
            $('process-status').textContent = `Failed to cancel: ${data.error || 'Unknown error'}`;
            btnCancel.disabled = false;
            btnCancel.textContent = 'Cancel';
        }
    } catch (err) {
        $('process-status').textContent = `Error cancelling: ${err.message}`;
        btnCancel.disabled = false;
        btnCancel.textContent = 'Cancel';
    }
}

function connectWebSocket(jobId) {
    if (wsConnection) wsConnection.close(); // Close existing connection if any
    if (pollInterval) clearInterval(pollInterval); // Stop polling if WebSocket is attempted

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    wsConnection = new WebSocket(`${wsProtocol}//${window.location.host}${API}/ws/status/${jobId}`);

    wsConnection.onopen = () => {
        console.log('WebSocket connected for job:', jobId);
        if (pollInterval) clearInterval(pollInterval); // Ensure polling is stopped
    };

    wsConnection.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleProcessUpdate(data);
    };

    wsConnection.onerror = (error) => {
        console.error('WebSocket error:', error);
        // Fallback to polling on error
        if (!pollInterval) {
            pollInterval = setInterval(pollStatus, 1000);
            console.log('Falling back to polling due to WebSocket error.');
        }
    };

    wsConnection.onclose = (event) => {
        console.log('WebSocket closed:', event.code, event.reason);
        // Fallback to polling if WebSocket closes unexpectedly and job is not completed/errored
        if (!pollInterval && !['completed', 'error', 'cancelled'].includes($('process-status').textContent.toLowerCase())) {
            pollInterval = setInterval(pollStatus, 1000);
            console.log('Falling back to polling due to WebSocket close.');
        }
    };
}

function handleProcessUpdate(data) {
    const p = data.progress;

    // Progress
    $('process-percent').textContent = `${p.percent}%`;
    $('process-fill').style.width = `${p.percent}%`;
    $('process-status').textContent = p.status || '';

    // ETA from server
    if (p.eta && p.eta !== '\u2014') {
        $('proc-eta').textContent = p.eta;
    } else if (currentEstimate && p.percent > 0) {
        // Client-side ETA calculation
        const elapsed = (Date.now() - processStartTime) / 1000;
        const totalEst = elapsed / (p.percent / 100);
        const remaining = Math.max(0, totalEst - elapsed);
        $('proc-eta').textContent = fmtTime(remaining);
    }

    // Step icons
    const workSteps = document.querySelectorAll('.work-step');
    workSteps.forEach((item, i) => {
        const icon = item.querySelector('.step-icon');
        if (i < p.step - 1) {
            item.classList.add('done'); item.classList.remove('active');
            icon.textContent = '\u2705';
        } else if (i === p.step - 1) {
            item.classList.add('active'); item.classList.remove('done');
            icon.textContent = '\u{1F504}';
        }
    });

    // Completed
    if (data.status === 'completed') {
        if (pollInterval) clearInterval(pollInterval);
        if (wsConnection) wsConnection.close();
        clearInterval(elapsedTimer);
        $('proc-eta').textContent = '00:00';
        workSteps.forEach(item => {
            item.classList.add('done'); item.classList.remove('active');
            item.querySelector('.step-icon').textContent = '\u2705';
        });
        $('process-percent').textContent = '100%';
        $('process-fill').style.width = '100%';

        // Show actual time
        if (data.result?.stats) {
            $('proc-elapsed').textContent = data.result.stats.elapsed_formatted;
            $('process-status').textContent = `Done! ${data.result.stats.speed} speed`;
        }
        btnCancel.classList.add('hidden');
        setTimeout(() => showResult(data.result), 800);
    } else if (data.status === 'error') {
        if (pollInterval) clearInterval(pollInterval);
        if (wsConnection) wsConnection.close();
        clearInterval(elapsedTimer);
        $('process-status').textContent = `Error: ${data.error}`;
        $('process-percent').textContent = '\u274C';
        btnCancel.classList.add('hidden');
    } else if (data.status === 'cancelled') {
        if (pollInterval) clearInterval(pollInterval);
        if (wsConnection) wsConnection.close();
        clearInterval(elapsedTimer);
        $('process-status').textContent = 'Processing cancelled.';
        $('process-percent').textContent = '\u274C';
        btnCancel.classList.add('hidden');
    }
}


function setupProcessSteps(body) {
    const container = $('process-steps'); container.innerHTML = '';
    const enc = body.encoder === 'libx264' ? 'CPU' : 'GPU';
    const hdr = document.createElement('div'); hdr.className = 'process-step-item header-step';
    hdr.innerHTML = `<span class="step-icon">\u2699</span> <span>${enc} (${body.encoder}) | ${selectedPreset.toUpperCase()}</span>`;
    container.appendChild(hdr);

    const steps = body.parallel && body.qualities.length > 1
        ? [`Parallel: ${body.qualities.join(', ')}`]
        : body.qualities.map(q => `${q} transcode`);
    steps.push('Master playlist');
    if (body.generate_thumbnail) steps.push('Thumbnails');

    steps.forEach((s, i) => {
        const div = document.createElement('div'); div.className = 'process-step-item work-step'; div.id = `pstep-${i}`;
        div.innerHTML = `<span class="step-icon">\u23F3</span> <span class="step-text">${s}</span>`;
        container.appendChild(div);
    });
}

// This function is now a fallback for when WebSocket fails or is not supported
async function pollStatus() {
    try {
        const res = await fetch(`${API}/api/status/${currentJobId}`);
        if (!res.ok) {
            if (pollInterval) clearInterval(pollInterval);
            $('process-status').textContent = `Error fetching status: ${res.statusText}`;
            btnCancel.classList.add('hidden');
            return;
        }
        const data = await res.json();
        handleProcessUpdate(data); // Use the same handler for polling updates
    } catch (err) {
        console.warn('Poll error:', err);
        if (pollInterval) clearInterval(pollInterval);
        $('process-status').textContent = `Network error during polling: ${err.message}`;
        btnCancel.classList.add('hidden');
    }
}

// ═══════ RESULT ═══════
function showResult(result) {
    if (!result) {
        $('process-status').textContent = 'No result found';
        return;
    }
    showStep(stepResult);
    const video = $('hls-player');
    const url = result.master_playlist;
    if (Hls.isSupported()) {
        const hls = new Hls({ debug: false, enableWorker: true });
        hls.loadSource(url); hls.attachMedia(video);
        hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = url; video.addEventListener('loadedmetadata', () => video.play().catch(() => {}));
    }

    const statsDiv = $('result-stats');
    if (result.stats) {
        const s = result.stats;
        statsDiv.innerHTML = `<div class="stats-grid">
            <div class="stat-card"><span class="stat-value">${s.elapsed_formatted || '\u2014'}</span><span class="stat-label">Time spent</span></div>
            <div class="stat-card"><span class="stat-value">${s.speed || '\u2014'}</span><span class="stat-label">Speed</span></div>
            <div class="stat-card"><span class="stat-value">${s.output_size_mb || 0} MB</span><span class="stat-label">Output size</span></div>
            <div class="stat-card"><span class="stat-value">${s.compression_ratio || 0}x</span><span class="stat-label">Compression</span></div>
        </div>`;
    }

    const metaDiv = $('result-meta'); metaDiv.innerHTML = '';
    if (result.encoding) { addBadge(metaDiv, `${result.encoding.mode} (${result.encoding.encoder})`); addBadge(metaDiv, `Preset: ${result.encoding.preset_label}`); if (result.encoding.parallel) addBadge(metaDiv, 'Parallel'); }
    if (result.trim?.trimmed) addBadge(metaDiv, `Trimmed: ${result.trim.duration.toFixed(1)}s`);
    if (result.audio?.normalized) addBadge(metaDiv, 'Audio normalized');
    result.qualities.forEach(q => addBadge(metaDiv, `${q.name} (${q.size_mb}MB)`));
    if (result.thumbnails?.length) addBadge(metaDiv, `${result.thumbnails.length} thumbnail`);

    $('manifest-url').textContent = `${window.location.origin}${url}`;
    btnDownload.onclick = () => window.open(`${API}/api/download/${result.video_id}`, '_blank');
}

function addBadge(c, t) { const s = document.createElement('span'); s.className = 'result-badge'; s.textContent = t; c.appendChild(s); }

btnCopy.addEventListener('click', () => {
    navigator.clipboard.writeText($('manifest-url').textContent).then(() => {
        btnCopy.textContent = '\u2705'; setTimeout(() => btnCopy.textContent = '\u{1f4cb}', 2000);
    });
});

btnNew.addEventListener('click', () => {
    currentVideoId = null; currentJobId = null; videoInfo = null; qualityAnalysis = null;
    selectedQualities.clear(); selectedPreset = 'balanced'; currentEstimate = null;
    processStartTime = null;
    if (pollInterval) clearInterval(pollInterval);
    if (elapsedTimer) clearInterval(elapsedTimer);
    if (wsConnection) wsConnection.close(); // Close WebSocket on new job
    document.querySelector('.drop-zone-content').style.display = '';
    $('upload-progress').classList.add('hidden');
    $('upload-fill').style.width = '0%';
    $('quality-analysis')?.classList.add('hidden');
    $('estimate-panel')?.classList.add('hidden');
    fileInput.value = '';
    $('trim-start').value = 0; $('trim-end').value = 0;
    btnCancel.classList.add('hidden'); // Hide cancel button
    btnCancel.disabled = false;
    btnCancel.textContent = 'Cancel';
    loadPresets();
    showStep(stepUpload);
});


// ═══════════════════════════════════════════════════════════
// CLOUDFLARE R2 STORAGE
// ═══════════════════════════════════════════════════════════

// R2 config: save and connect
$('btn-r2-save')?.addEventListener('click', async () => {
    const config = {
        account_id: $('r2-account-id')?.value?.trim(),
        access_key: $('r2-access-key')?.value?.trim(),
        secret_key: $('r2-secret-key')?.value?.trim(),
        bucket: $('r2-bucket')?.value?.trim(),
        public_url: $('r2-public-url')?.value?.trim() || '',
    };

    if (!config.account_id || !config.access_key || !config.secret_key || !config.bucket) {
        alert('Please fill in all required fields!');
        return;
    }

    const badge = $('r2-connection-status');
    badge.textContent = '⏳ Connecting...';
    badge.style.color = '#fbbf24';

    try {
        const res = await fetch('/api/r2/configure', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(config),
        });
        const data = await res.json();

        if (data.test?.ok) {
            badge.textContent = '✅ Connected: ' + config.bucket;
            badge.style.color = '#34d399';
            // Save to localStorage (secret key is NOT saved!)
            localStorage.setItem('r2_config', JSON.stringify({
                account_id: config.account_id,
                access_key: config.access_key,
                bucket: config.bucket,
                public_url: config.public_url,
            }));
        } else {
            badge.textContent = '❌ Error: ' + (data.test?.error || data.error || 'Unknown');
            badge.style.color = '#f87171';
        }
    } catch (e) {
        badge.textContent = '❌ Server error';
        badge.style.color = '#f87171';
    }
});

// Upload video to R2
$('btn-r2-upload')?.addEventListener('click', async () => {
    if (!currentVideoId) return;

    // Check if R2 is configured
    const statusRes = await fetch('/api/r2/status');
    const statusData = await statusRes.json();
    if (!statusData.configured) {
        alert('Please configure Cloudflare R2 first (below)!');
        document.getElementById('step-r2')?.scrollIntoView({behavior: 'smooth'});
        return;
    }

    const btn = $('btn-r2-upload');
    btn.disabled = true;
    btn.innerHTML = '<span>⏳ Uploading...</span>';

    const statusDiv = $('r2-upload-status');
    statusDiv.classList.remove('hidden');
    statusDiv.innerHTML = '<div class="spinner-small"></div> Starting R2 upload...';

    try {
        const res = await fetch(`/api/r2/upload/${currentVideoId}`, {method: 'POST'});
        const data = await res.json();

        // Poll upload status
        const pollR2 = setInterval(async () => {
            try {
                const sr = await fetch(`/api/r2/upload-status/${currentVideoId}`);
                const sd = await sr.json();

                if (sd.status === 'uploading') {
                    const p = sd.progress;
                    statusDiv.innerHTML = `
                        <div style="display:flex;align-items:center;gap:0.5rem;">
                            <div class="spinner-small"></div>
                            <span>☁️ ${p.uploaded}/${p.total} files (${p.percent}%) — ${p.current_file}</span>
                        </div>
                        <div style="height:4px;background:#1e293b;border-radius:4px;margin-top:0.5rem;">
                            <div style="height:100%;width:${p.percent}%;background:linear-gradient(90deg,#f38020,#f6821f);border-radius:4px;transition:width 0.3s;"></div>
                        </div>`;
                } else if (sd.status === 'completed') {
                    clearInterval(pollR2);
                    const r = sd.result;
                    statusDiv.innerHTML = `
                        <div style="color:#34d399;font-weight:600;">✅ Uploaded! ${r.uploaded} files (${r.total_mb} MB)</div>
                        <div style="margin-top:0.5rem;">
                            <label style="font-size:0.8rem;color:#94a3b8;">HLS URL:</label>
                            <code style="display:block;padding:0.5rem;background:#0f172a;border-radius:6px;word-break:break-all;font-size:0.85rem;margin-top:0.3rem;">${r.master_url}</code>
                        </div>`;
                    btn.disabled = false;
                    btn.innerHTML = '<span>✅ Uploaded</span>';
                } else if (sd.status === 'error') {
                    clearInterval(pollR2);
                    statusDiv.innerHTML = `<div style="color:#f87171;">❌ Error: ${sd.error}</div>`;
                    btn.disabled = false;
                    btn.innerHTML = '<span>☁️ Upload to R2</span>';
                }
            } catch (e) {}
        }, 1000);
    } catch (e) {
        statusDiv.innerHTML = `<div style="color:#f87171;">❌ ${e.message}</div>`;
        btn.disabled = false;
        btn.innerHTML = '<span>☁️ R2 ga yuklash</span>';
    }
});

// Restore saved R2 config on page load
(function loadR2Config() {
    try {
        const saved = JSON.parse(localStorage.getItem('r2_config') || '{}');
        if (saved.account_id) $('r2-account-id').value = saved.account_id;
        if (saved.access_key) $('r2-access-key').value = saved.access_key;
        if (saved.bucket) $('r2-bucket').value = saved.bucket;
        if (saved.public_url) $('r2-public-url').value = saved.public_url;
    } catch(e) {}
})();


// ═══════════════════════════════════════════════════════════
// BATCH UPLOAD & AUTO-PROCESS
// ═══════════════════════════════════════════════════════════

(function initBatch() {
    const dropzone = $('batch-dropzone');
    const fileInput = $('batch-file-input');
    const settings = $('batch-settings');
    const fileList = $('batch-file-list');
    const btnStart = $('btn-batch-start');
    const btnCancel2 = $('btn-batch-cancel');
    const progressDiv = $('batch-progress');
    const barFill = $('batch-bar-fill');
    const statusText = $('batch-status-text');
    const itemsDiv = $('batch-items-status');

    let selectedFiles = [];
    let currentBatchId = null;

    if (!dropzone) return;

    // Drop zone click
    dropzone.addEventListener('click', () => fileInput.click());

    // Drag and drop
    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.style.borderColor = 'var(--accent)';
    });
    dropzone.addEventListener('dragleave', () => {
        dropzone.style.borderColor = '';
    });
    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.style.borderColor = '';
        handleFiles(e.dataTransfer.files);
    });

    fileInput.addEventListener('change', () => handleFiles(fileInput.files));

    function handleFiles(files) {
        selectedFiles = Array.from(files).slice(0, 20);
        if (selectedFiles.length === 0) return;

        settings.style.display = 'block';
        fileList.innerHTML = selectedFiles.map((f, i) => {
            const sizeMB = (f.size / (1024 * 1024)).toFixed(1);
            return `<div style="display:flex;justify-content:space-between;padding:0.4rem 0;border-bottom:1px solid rgba(255,255,255,0.06);font-size:0.9rem;">
                <span style="color:var(--text-primary);">${i + 1}. ${f.name}</span>
                <span style="color:var(--text-secondary);">${sizeMB} MB</span>
            </div>`;
        }).join('');
    }

    // Start batch
    btnStart.addEventListener('click', async () => {
        if (selectedFiles.length === 0) return;

        const qualities = $('batch-quality').value;
        const preset = $('batch-preset').value;

        btnStart.disabled = true;
        btnStart.textContent = 'Uploading...';
        btnCancel2.classList.remove('hidden');
        progressDiv.style.display = 'block';
        statusText.textContent = `Uploading ${selectedFiles.length} files...`;
        barFill.style.width = '0%';

        const formData = new FormData();
        selectedFiles.forEach(f => formData.append('files', f));

        try {
            const resp = await fetch(`/api/batch/upload?qualities=${encodeURIComponent(qualities)}&encoding_preset=${preset}`, {
                method: 'POST',
                body: formData,
            });

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || 'Upload failed');
            }

            const data = await resp.json();
            currentBatchId = data.batch_id;

            if (data.upload_errors && data.upload_errors.length > 0) {
                statusText.textContent = `${data.total_files} uploaded (${data.upload_errors.length} errors), processing...`;
            } else {
                statusText.textContent = `${data.total_files} files uploaded, processing...`;
            }

            btnStart.textContent = 'Processing...';

            // Start polling
            pollBatchStatus(currentBatchId);
        } catch (e) {
            statusText.textContent = `❌ Error: ${e.message}`;
            btnStart.disabled = false;
            btnStart.textContent = '🚀 Start Batch Processing';
        }
    });

    // Cancel batch
    btnCancel2.addEventListener('click', async () => {
        if (!currentBatchId) return;
        try {
            await fetch(`/api/batch/cancel/${currentBatchId}`, { method: 'POST' });
            statusText.textContent = 'Batch cancelled';
        } catch (e) { /* ignore */ }
    });

    function pollBatchStatus(batchId) {
        const poll = setInterval(async () => {
            try {
                const resp = await fetch(`/api/batch/status/${batchId}`);
                const data = await resp.json();

                barFill.style.width = data.percent + '%';

                const statusIcons = { queued: '⏳', processing: '⚡', completed: '✅', error: '❌', cancelled: '⏹️' };

                itemsDiv.innerHTML = data.items.map((item, i) => {
                    const icon = statusIcons[item.status] || '⏳';
                    const extra = item.status === 'error' ? ` — ${item.error}` : '';
                    return `<div style="display:flex;justify-content:space-between;padding:0.3rem 0;font-size:0.85rem;border-bottom:1px solid rgba(255,255,255,0.04);">
                        <span>${icon} ${item.filename}</span>
                        <span style="color:var(--text-secondary);">${item.status}${extra}</span>
                    </div>`;
                }).join('');

                if (data.current_progress && data.status === 'processing') {
                    statusText.textContent = data.current_progress.status || 'Processing...';
                }

                if (data.status !== 'processing') {
                    clearInterval(poll);
                    const elapsed = data.elapsed ? (data.elapsed / 60).toFixed(1) : '?';
                    statusText.textContent = `✅ Batch complete: ${data.completed}/${data.total} success, ${data.failed} failed (${elapsed} min)`;
                    barFill.style.width = '100%';
                    btnStart.disabled = false;
                    btnStart.textContent = '🚀 Start Batch Processing';
                    btnCancel2.classList.add('hidden');
                }
            } catch (e) {
                // retry on error
            }
        }, 1500);
    }
})();
