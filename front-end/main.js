const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

let mainWindow;
let backendProcess = null;

const repoRoot = path.join(__dirname, '..');
const modelPath = path.join(repoRoot, 'models', 'model-gen.safetensors');
const backendExe = path.join(repoRoot, 'aria_backend.exe');
const pluginExe = path.join(repoRoot, 'Aria Bridge.exe');
const isDev = !fs.existsSync(backendExe);

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
        if (!fs.existsSync(modelPath)) {
            mainWindow.webContents.send('status', 'STATUS:error:model_missing');
        }
    });
}

app.whenReady().then(createWindow);

ipcMain.handle('launch-backend', async (event, mode) => {
    if (backendProcess) {
        backendProcess.kill();
        backendProcess = null;
    }

    const args = [
        mode,
        '--checkpoint', modelPath,
        '--device', 'cuda',
        '--feedback',
        '--data-dir', path.join(repoRoot, 'data'),
    ];

    let proc;
    if (isDev) {
        proc = spawn('python', [path.join(repoRoot, 'real-time', 'ableton_bridge.py'), ...args], {
            cwd: repoRoot,
        });
    } else {
        proc = spawn(backendExe, args);
    }

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
    const args = [
        'plugin',
        '--checkpoint', modelPath,
        '--device', 'cuda',
        '--feedback',
        '--data-dir', path.join(repoRoot, 'data'),
    ];

    // Start backend detached so it survives the launcher closing
    let proc;
    if (isDev) {
        proc = spawn('python', [path.join(repoRoot, 'real-time', 'ableton_bridge.py'), ...args], {
            cwd: repoRoot,
            detached: true,
            stdio: 'ignore',
        });
    } else {
        proc = spawn(backendExe, args, { detached: true, stdio: 'ignore' });
    }
    proc.unref();

    if (fs.existsSync(pluginExe)) {
        spawn(pluginExe, [], { detached: true, stdio: 'ignore' }).unref();
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
