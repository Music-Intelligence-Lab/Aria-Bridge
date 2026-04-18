const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
    launchBackend: (mode) => ipcRenderer.invoke('launch-backend', mode),
    launchStandalone: () => ipcRenderer.invoke('launch-standalone'),
    quit: () => ipcRenderer.invoke('quit'),
    minimize: () => ipcRenderer.invoke('minimize'),
    onStatus: (callback) => ipcRenderer.on('status', (_event, line) => callback(line)),
    onLog: (callback) => ipcRenderer.on('log', (_event, msg) => callback(msg)),
});
