// Screen navigation
let currentMode = null;

function showScreen(id) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(id).classList.add('active');
}

// Mode selection
document.getElementById('btn-m4l').addEventListener('click', () => {
    currentMode = 'm4l';
    launchBackend('m4l');
});

document.getElementById('btn-plugin').addEventListener('click', () => {
    showScreen('screen-plugin');
});

// Plugin sub-selection
document.getElementById('btn-standalone').addEventListener('click', () => {
    window.api.launchStandalone();
});

document.getElementById('btn-vst3').addEventListener('click', () => {
    showScreen('screen-vst3');
});

document.getElementById('btn-back-plugin').addEventListener('click', () => {
    showScreen('screen-mode');
});

// VST3 waiting
document.getElementById('btn-vst3-continue').addEventListener('click', () => {
    currentMode = 'plugin';
    launchBackend('plugin');
});

document.getElementById('btn-back-vst3').addEventListener('click', () => {
    showScreen('screen-plugin');
});

// Backend launch
function launchBackend(mode) {
    const modeNames = { 'm4l': 'M4L Device', 'plugin': 'VST3 Plugin' };
    document.getElementById('mode-label').textContent = 'Mode: ' + (modeNames[mode] || mode);
    showScreen('screen-status');
    setStatus('idle', 'Starting...');
    window.api.launchBackend(mode);
}

// Status handling
const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const portsRow = document.getElementById('ports-row');
const genSection = document.getElementById('gen-section');
const genBar = document.getElementById('gen-bar');
const genLabel = document.getElementById('gen-label');
const playSection = document.getElementById('play-section');
const playBar = document.getElementById('play-bar');
const playLabel = document.getElementById('play-label');
const errorBox = document.getElementById('error-box');

let genStartTime = null;
let genTimerHandle = null;
let playTotalDuration = null;
let playStartProgress = null;
let playStartTime = null;

function setStatus(state, label) {
    statusDot.className = 'dot dot-' + state;
    statusText.textContent = label;
}

function startGenTimer() {
    genStartTime = Date.now();
    if (genTimerHandle) clearInterval(genTimerHandle);
    genTimerHandle = setInterval(() => {
        const elapsed = (Date.now() - genStartTime) / 1000;
        genLabel.textContent = `Generating... ${elapsed.toFixed(1)}s elapsed`;
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

window.api.onStatus((line) => {
    const parts = line.split(':');
    const type = parts[1];
    const value = parts.slice(2).join(':');

    switch (type) {
        case 'ports_ready':
            portsRow.classList.remove('hidden');
            break;

        case 'ready':
            setStatus('ready', 'Ready');
            errorBox.classList.add('hidden');
            break;

        case 'generating':
            if (value === '' || value === undefined) {
                // Generation started
                setStatus('generating', 'Generating');
                genBar.classList.add('indeterminate');
                genLabel.textContent = 'Generating... 0.0s elapsed';
                genSection.classList.remove('hidden');
                playSection.classList.add('hidden');
                startGenTimer();
            } else {
                // Elapsed time update from Python
                const elapsed = parseFloat(value);
                if (!isNaN(elapsed)) {
                    genLabel.textContent = `Generating... ${elapsed.toFixed(1)}s elapsed`;
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

        case 'play_duration':
            playTotalDuration = parseFloat(value) || null;
            break;

        case 'playing': {
            const progress = parseFloat(value);
            if (isNaN(progress)) break;
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
            playSection.classList.add('hidden');
            genSection.classList.add('hidden');
            playBar.style.width = '0%';
            playTotalDuration = null;
            setStatus('ready', 'Ready');
            break;

        case 'error': {
            const msg = value === 'model_missing'
                ? 'Model not found. Place model-gen.safetensors in the models/ folder.'
                : value;
            errorBox.textContent = '⚠ ' + msg;
            errorBox.classList.remove('hidden');
            setStatus('error', 'Error');
            stopGenTimer();
            break;
        }
    }
});
