let currentMode = null;

function showScreen(id) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(id).classList.add('active');
}

document.getElementById('btn-m4l').addEventListener('click', () => {
    currentMode = 'm4l';
    launchBackend('m4l');
});

document.getElementById('btn-plugin').addEventListener('click', () => {
    showScreen('screen-plugin');
});

document.getElementById('btn-standalone').addEventListener('click', () => {
    currentMode = 'standalone';
    document.getElementById('mode-label').textContent = 'Mode: Standalone';
    showScreen('screen-status');
    setStatus('idle', 'Loading model...');
    logActivity('Starting Aria Bridge standalone...');
    window.api.launchStandalone();
});

document.getElementById('btn-vst3').addEventListener('click', () => {
    showScreen('screen-vst3');
});

document.getElementById('btn-back-plugin').addEventListener('click', () => {
    showScreen('screen-mode');
});

document.getElementById('btn-vst3-continue').addEventListener('click', () => {
    currentMode = 'plugin';
    launchBackend('plugin');
});

document.getElementById('btn-back-vst3').addEventListener('click', () => {
    showScreen('screen-plugin');
});

function launchBackend(mode) {
    const modeNames = { 'm4l': 'M4L Device', 'plugin': 'VST3 Plugin' };
    document.getElementById('mode-label').textContent = 'Mode: ' + (modeNames[mode] || mode);
    showScreen('screen-status');
    setStatus('idle', 'Starting...');
    window.api.launchBackend(mode);
}

// DOM refs
const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const portsRow = document.getElementById('ports-row');
const genSection = document.getElementById('gen-section');
const genBar = document.getElementById('gen-bar');
const genLabel = document.getElementById('gen-label');
const awaitingPlayRow = document.getElementById('awaiting-play-row');
const playSection = document.getElementById('play-section');
const playBar = document.getElementById('play-bar');
const playLabel = document.getElementById('play-label');
const errorBox = document.getElementById('error-box');
const activityLog = document.getElementById('activity-log');

let genStartTime = null;
let genTimerHandle = null;
let playTotalDuration = null;

function setStatus(state, label) {
    statusDot.className = 'dot dot-' + state;
    statusText.textContent = label;
}

function logActivity(msg) {
    const entry = document.createElement('div');
    entry.className = 'activity-entry';
    entry.textContent = msg;
    activityLog.appendChild(entry);
    // Keep last 12 entries
    while (activityLog.children.length > 12) {
        activityLog.removeChild(activityLog.firstChild);
    }
    activityLog.scrollTop = activityLog.scrollHeight;
}

function startGenTimer() {
    genStartTime = Date.now();
    if (genTimerHandle) clearInterval(genTimerHandle);
    genTimerHandle = setInterval(() => {
        const elapsed = (Date.now() - genStartTime) / 1000;
        genLabel.textContent = `Generating... ${elapsed.toFixed(1)}s`;
    }, 100);
}

function stopGenTimer() {
    if (genTimerHandle) {
        clearInterval(genTimerHandle);
        genTimerHandle = null;
    }
}

function formatRemaining(seconds) {
    const s = Math.max(0, Math.round(seconds));
    const m = Math.floor(s / 60);
    const sec = String(s % 60).padStart(2, '0');
    return `${m}:${sec}`;
}

const awaitingPlayText = document.getElementById('awaiting-play-text');
const paramLabels = { temp: 'Temp', top_p: 'Top-p', min_p: 'Min-p', tokens: 'Tokens' }
const feedbackLabels = { coherence: 'Coherence', repetition: 'Repetition', taste: 'Taste', continuity: 'Continuity' };

window.api.onStatus((line) => {
    const parts = line.split(':');
    const type = parts[1];
    const value = parts.slice(2).join(':');

    switch (type) {
        case 'ports_ready':
            portsRow.classList.remove('hidden');
            logActivity('✓ MIDI ports connected');
            break;

        case 'ready':
            setStatus('ready', 'Ready');
            errorBox.classList.add('hidden');
            logActivity('✓ Model loaded — running');
            break;

        case 'synced':
            logActivity(`✓ Synced — ${value}`);
            break;

        case 'param': {
            // value = "temp:0.90" or "tokens:256"
            const colonIdx = value.indexOf(':');
            const pname = value.slice(0, colonIdx);
            const pval = value.slice(colonIdx + 1);
            const label = paramLabels[pname] || pname;
            logActivity(`↳ ${label} → ${pval}`);
            break;
        }

        case 'generating':
            if (value === '' || value === undefined) {
                setStatus('generating', 'Generating');
                genBar.classList.add('indeterminate');
                genLabel.textContent = 'Generating... 0.0s';
                genSection.classList.remove('hidden');
                awaitingPlayRow.classList.add('hidden');
                playSection.classList.add('hidden');
                startGenTimer();
            } else {
                const elapsed = parseFloat(value);
                if (!isNaN(elapsed)) {
                    genLabel.textContent = `Generating... ${elapsed.toFixed(1)}s`;
                }
            }
            break;

        case 'generation_done':
            stopGenTimer();
            genBar.classList.remove('indeterminate');
            genBar.style.width = '100%';
            setStatus('ready', 'Ready');
            setTimeout(() => {
                genSection.classList.add('hidden');
                genBar.style.width = '0%';
            }, 600);
            break;

        case 'awaiting_play': {
            const playMsg = currentMode === 'm4l'
                ? 'Output ready — press Play in M4L'
                : 'Output ready — press Play in plugin';
            awaitingPlayText.textContent = playMsg;
            setStatus('ready', 'Ready');
            awaitingPlayRow.classList.remove('hidden');
            logActivity('▶ ' + playMsg);
            break;
        }

        case 'play_duration':
            playTotalDuration = parseFloat(value) || null;
            break;

        case 'playing': {
            const progress = parseFloat(value);
            if (isNaN(progress)) break;
            awaitingPlayRow.classList.add('hidden');
            playBar.style.width = (progress * 100).toFixed(1) + '%';
            setStatus('playing', 'Playing');
            playSection.classList.remove('hidden');
            genSection.classList.add('hidden');
            stopGenTimer();
            if (playTotalDuration !== null) {
                const remaining = playTotalDuration * (1 - progress);
                playLabel.textContent = `Playing — ${formatRemaining(remaining)} left`;
            } else {
                playLabel.textContent = 'Playing...';
            }
            break;
        }

        case 'stopped':
            stopGenTimer();
            awaitingPlayRow.classList.add('hidden');
            playSection.classList.add('hidden');
            genSection.classList.add('hidden');
            playBar.style.width = '0%';
            playTotalDuration = null;
            setStatus('ready', 'Ready');
            break;

        case 'feedback': {
            // value = "grade:4" | "coherence:0.80" | "commit"
            const fIdx = value.indexOf(':');
            const fKey = fIdx === -1 ? value : value.slice(0, fIdx);
            const fVal = fIdx === -1 ? '' : value.slice(fIdx + 1);
            if (fKey === 'grade') {
                logActivity(`★ Grade set: ${fVal}/5`);
            } else if (fKey === 'commit') {
                logActivity('✓ Feedback committed');
            } else {
                const label = feedbackLabels[fKey] || fKey;
                logActivity(`↳ ${label}: ${fVal}`);
            }
            break;
        }

        case 'error':
            errorBox.textContent = '⚠ ' + value;
            errorBox.classList.remove('hidden');
            setStatus('error', 'Error');
            stopGenTimer();
            logActivity('⚠ ' + value);
            break;
    }
});

// Stderr log lines (token progress, errors from model)
window.api.onLog((msg) => {
    logActivity('» ' + msg);
});
