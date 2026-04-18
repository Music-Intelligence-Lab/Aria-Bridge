const { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

let mainWindow;
let backendProcess = null;
let tray = null;

// userBase: folder where the user placed the launcher (next to models/, ableton/, etc.)
//   Windows portable: electron-builder sets PORTABLE_EXECUTABLE_DIR
//   Mac: parent of the .app bundle (3 levels up from the binary)
//   Dev: repo root
function userBase() {
    if (!app.isPackaged) return path.join(__dirname, '..');
    if (process.platform === 'darwin') return path.resolve(process.execPath, '../../../..');
    return path.dirname(process.execPath);
}

// resourcesBase: where electron-builder places extraResources (bundled executables)
function resourcesBase() {
    return app.isPackaged ? process.resourcesPath : path.join(__dirname, '..');
}

function findModel() {
    const modelsDir = path.join(userBase(), 'models');
    if (!fs.existsSync(modelsDir)) return null;
    const hit = fs.readdirSync(modelsDir)
        .filter(f => f.endsWith('.safetensors') || f.endsWith('.gen'))
        .sort((a, b) => fs.statSync(path.join(modelsDir, b)).mtimeMs - fs.statSync(path.join(modelsDir, a)).mtimeMs)[0];
    return hit ? path.join(modelsDir, hit) : null;
}

function backendExe() {
    const name = process.platform === 'darwin' ? 'aria_backend' : 'aria_backend.exe';
    return path.join(resourcesBase(), name);
}

function pluginAppPath() {
    if (process.platform === 'darwin') return path.join(resourcesBase(), 'Aria Bridge.app');
    return path.join(resourcesBase(), 'Aria Bridge.exe');
}

function appIconPath() {
    return path.join(__dirname, 'assets', 'icon.ico');
}

function showTray() {
    if (tray) return;
    try {
        tray = new Tray(nativeImage.createFromPath(appIconPath()));
        tray.setToolTip('Aria Bridge — running');
        tray.setContextMenu(Menu.buildFromTemplate([
            {
                label: 'Show Window',
                click: () => {
                    if (mainWindow && !mainWindow.isDestroyed()) {
                        mainWindow.show();
                        mainWindow.focus();
                    }
                },
            },
            { type: 'separator' },
            {
                label: 'Quit Aria',
                click: () => {
                    if (backendProcess) {
                        backendProcess.kill();
                        backendProcess = null;
                    }
                    hideTray();
                    app.quit();
                },
            },
        ]));
        tray.on('click', () => {
            if (mainWindow && !mainWindow.isDestroyed()) {
                mainWindow.show();
                mainWindow.focus();
            }
        });
    } catch (e) {
        console.error('[tray] failed to create tray icon:', e.message);
    }
}

function hideTray() {
    if (tray) {
        tray.destroy();
        tray = null;
    }
}

// Attach stdout/stderr/close listeners to a backend process.
// On close, hides the tray and either quits silently (window hidden) or
// shows an error in the UI (window visible).
function attachBackendListeners(proc) {
    let buffer = '';
    proc.stdout.on('data', (data) => {
        buffer += data.toString();
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('STATUS:') && mainWindow && !mainWindow.isDestroyed()) {
                mainWindow.webContents.send('status', trimmed);
            }
        }
    });

    let stderrBuf = '';
    proc.stderr.on('data', (data) => {
        stderrBuf += data.toString();
        const lines = stderrBuf.split('\n');
        stderrBuf = lines.pop();
        for (const line of lines) {
            const raw = line.trim();
            if (!raw) continue;
            console.error('[backend]', raw);
            const clean = raw.replace(/\x1b\[[0-9;]*m/g, '').trim();
            if (clean && mainWindow && !mainWindow.isDestroyed() && (
                clean.includes('it/s') ||
                clean.includes('token') ||
                clean.includes('Token') ||
                clean.includes('ERROR') ||
                clean.includes('Generation')
            )) {
                mainWindow.webContents.send('log', clean);
            }
        }
    });

    proc.on('close', (code) => {
        backendProcess = null;
        hideTray();
        if (mainWindow && !mainWindow.isDestroyed() && code !== 0 && code !== null) {
            mainWindow.webContents.send('status', `STATUS:error:backend exited (code ${code})`);
        }
    });
}

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 800,
        height: 520,
        frame: false,
        center: true,
        alwaysOnTop: true,
        resizable: false,
        icon: appIconPath(),
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
        },
    });

    mainWindow.loadFile('renderer/index.html');

    mainWindow.on('closed', () => {
        mainWindow = null;
    });

    mainWindow.webContents.on('did-finish-load', () => {
        if (!findModel()) {
            const modelsDir = path.join(userBase(), 'models');
            mainWindow.webContents.send('status', `STATUS:error:No model found. Place a .safetensors file in: ${modelsDir}`);
        }
    });
}

app.whenReady().then(() => {
    if (process.platform === 'win32') app.setAppUserModelId('com.aria.bridge');
    createWindow();
});

function feedbackDir() { return path.join(userBase(), 'feedback'); }

function spawnBackend(mode, opts = {}) {
    const model = findModel();
    const args = model
        ? [mode, '--checkpoint', model, '--feedback', '--data-dir', feedbackDir()]
        : [mode, '--feedback', '--data-dir', feedbackDir()];
    if (process.platform !== 'darwin') args.push('--device', 'cuda');

    // windowsHide: true prevents console window and stops the child from
    // inheriting Electron's open handle to app.asar (which would keep the
    // file locked even after Electron exits).
    const base = { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], ...opts };

    if (app.isPackaged) {
        return spawn(backendExe(), args, base);
    } else {
        return spawn('python', [path.join(resourcesBase(), 'real-time', 'ableton_bridge.py'), ...args], {
            cwd: resourcesBase(),
            ...base,
        });
    }
}

// M4L and VST3 modes: launch backend, keep window open showing live status.
ipcMain.handle('launch-backend', async (event, mode) => {
    if (backendProcess) {
        backendProcess.kill();
        backendProcess = null;
    }

    const proc = spawnBackend(mode);
    backendProcess = proc;
    showTray();
    attachBackendListeners(proc);

    return { started: true };
});

// Standalone mode: launch JUCE standalone app + backend, keep window open.
ipcMain.handle('launch-standalone', async () => {
    if (backendProcess) {
        backendProcess.kill();
        backendProcess = null;
    }

    // Launch JUCE standalone app as a fully independent process.
    const appPath = pluginAppPath();
    if (fs.existsSync(appPath)) {
        if (process.platform === 'darwin') {
            spawn('open', ['-n', appPath], { detached: true, stdio: 'ignore' }).unref();
        } else {
            spawn(appPath, [], { detached: true, stdio: 'ignore' }).unref();
        }
    }

    // Launch backend attached so we can monitor its output and show it in the UI.
    const proc = spawnBackend('plugin');
    backendProcess = proc;
    showTray();
    attachBackendListeners(proc);
});

ipcMain.handle('minimize', () => {
    if (mainWindow) mainWindow.minimize();
});

ipcMain.handle('quit', () => {
    if (backendProcess) {
        backendProcess.kill();
        backendProcess = null;
    }
    hideTray();
    app.quit();
});

app.on('window-all-closed', () => {
    if (backendProcess) {
        backendProcess.kill();
        backendProcess = null;
    }
    hideTray();
    if (process.platform !== 'darwin') app.quit();
});
