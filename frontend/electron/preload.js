"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
  selectFolder: async () => {
    try {
      const result = await ipcRenderer.invoke("axiom:select-folder");
      if (result && result.canceled) {
        return null;
      }
      if (result && result.filePaths && result.filePaths.length > 0) {
        return result.filePaths[0];
      }
      return null;
    } catch (error) {
      console.error("[AxiomTrace] selectFolder failed:", error.message);
      return null;
    }
  },

  selectFile: async () => {
    try {
      const result = await ipcRenderer.invoke("axiom:select-file");
      if (result && result.canceled) {
        return null;
      }
      if (result && result.filePaths && result.filePaths.length > 0) {
        return result.filePaths[0];
      }
      return null;
    } catch (error) {
      console.error("[AxiomTrace] selectFile failed:", error.message);
      return null;
    }
  },

  readFile: async (filepath) => {
    try {
      return await ipcRenderer.invoke("axiom:read-file", filepath);
    } catch (error) {
      return { ok: false, path: filepath, error: error.message };
    }
  },

  writeFile: async (filepath, content) => {
    try {
      return await ipcRenderer.invoke("axiom:write-file", filepath, content);
    } catch (error) {
      return { ok: false, path: filepath, error: error.message };
    }
  },

  getAppInfo: async () => {
    try {
      return await ipcRenderer.invoke("axiom:app-info");
    } catch (error) {
      return { versions: {}, platform: "unknown", error: error.message };
    }
  },

  isElectron: true,
});
