const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
    launchBackend: (mode) => ipcRenderer.invoke('launch-backend', mode),
    launchStandalone: () => ipcRenderer.invoke('launch-standalone'),
    quit: () => ipcRenderer.invoke('quit'),
    onStatus: (callback) => ipcRenderer.on('status', (_event, line) => callback(line)),
});
