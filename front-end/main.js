const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

let mainWindow;
let backendProcess = null;

// When packaged by electron-builder, extraResources land in process.resourcesPath.
// In dev, resources sit one level above front-end/ (the repo root).
function resourcesBase() {
    return app.isPackaged ? process.resourcesPath : path.join(__dirname, '..');
}

function modelPath()   { return path.join(resourcesBase(), 'models', 'model-gen.safetensors'); }
function backendExe()  { return path.join(resourcesBase(), 'aria_backend.exe'); }
function pluginExe()   { return path.join(resourcesBase(), 'Aria Bridge.exe'); }

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 800,
        height: 520,
        frame: false,
        center: true,
        alwaysOnTop: true,
        resizable: false,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
        },
    });

    mainWindow.loadFile('renderer/index.html');

    mainWindow.webContents.on('did-finish-load', () => {
        if (!fs.existsSync(modelPath())) {
            mainWindow.webContents.send('status', 'STATUS:error:model_missing');
        }
    });
}

app.whenReady().then(createWindow);

function spawnBackend(mode, opts = {}) {
    const args = [
        mode,
        '--checkpoint', modelPath(),
        '--device', 'cuda',
        '--feedback',
    ];

    if (app.isPackaged) {
        return spawn(backendExe(), args, opts);
    } else {
        return spawn('python', [path.join(resourcesBase(), 'real-time', 'ableton_bridge.py'), ...args], {
            cwd: resourcesBase(),
            ...opts,
        });
    }
}

ipcMain.handle('launch-backend', async (event, mode) => {
    if (backendProcess) {
        backendProcess.kill();
        backendProcess = null;
    }

    const proc = spawnBackend(mode);
    backendProcess = proc;

    let buffer = '';
    proc.stdout.on('data', (data) => {
        buffer += data.toString();
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('STATUS:')) {
                mainWindow.webContents.send('status', trimmed);
            }
        }
    });

    proc.stderr.on('data', (data) => {
        console.error('[backend]', data.toString().trim());
    });

    proc.on('close', (code) => {
        backendProcess = null;
        if (code !== 0 && code !== null) {
            mainWindow.webContents.send('status', `STATUS:error:backend exited (code ${code})`);
        }
    });

    return { started: true };
});

ipcMain.handle('launch-standalone', async () => {
    // Start backend detached so it survives the launcher closing
    const proc = spawnBackend('plugin', { detached: true, stdio: 'ignore' });
    proc.unref();

    if (fs.existsSync(pluginExe())) {
        spawn(pluginExe(), [], { detached: true, stdio: 'ignore' }).unref();
    }

    app.quit();
});

ipcMain.handle('quit', () => {
    if (backendProcess) {
        backendProcess.kill();
        backendProcess = null;
    }
    app.quit();
});

app.on('window-all-closed', () => {
    if (backendProcess) {
        backendProcess.kill();
    }
    if (process.platform !== 'darwin') app.quit();
});
