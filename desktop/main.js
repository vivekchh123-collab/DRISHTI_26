/* DRISHTI desktop shell.
 *
 * Electron here is a window and a lifecycle manager, nothing more. The
 * application is the Python server; this starts it, waits for it to be ready,
 * points a window at it, and makes sure it dies when the window does.
 *
 * Three things are handled deliberately, because each is a way this class of
 * app normally fails:
 *
 *   1. The port is read from the server's own stdout rather than assumed. A
 *      hard-coded port means the app refuses to start whenever anything else
 *      holds it, which on a working laptop is often.
 *   2. The window waits for /healthz rather than for a timer. Loading the URL
 *      too early gives the user a connection-refused page and no way to know
 *      it will work in two seconds.
 *   3. The child process is killed on every exit path. An orphaned server
 *      holding a port after the window closes is the classic Electron-plus-
 *      sidecar bug.
 */

const { app, BrowserWindow, shell, dialog, Menu } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");
const fs = require("fs");

let serverProcess = null;
let mainWindow = null;
let serverPort = null;

const isDev = !app.isPackaged;

/** Where the frozen backend lives, packaged or in development. */
function serverExecutable() {
  const exeName = process.platform === "win32"
    ? "drishti-server.exe" : "drishti-server";
  const candidates = isDev
    ? [path.join(__dirname, "..", "dist", "drishti-server", exeName)]
    : [path.join(process.resourcesPath, "server", exeName),
       path.join(process.resourcesPath, exeName)];
  return candidates.find((p) => fs.existsSync(p)) || candidates[0];
}

function startServer() {
  return new Promise((resolve, reject) => {
    const exe = serverExecutable();
    if (!fs.existsSync(exe)) {
      reject(new Error(
        `Backend not found at:\n${exe}\n\n` +
        `Build it first:\n    python scripts/build_desktop.py --server-only`));
      return;
    }

    serverProcess = spawn(exe, [], {
      cwd: path.dirname(exe),
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let settled = false;
    let buffer = "";

    serverProcess.stdout.on("data", (chunk) => {
      buffer += chunk.toString();
      const m = buffer.match(/DRISHTI_PORT=(\d+)/);
      if (m && !settled) {
        settled = true;
        serverPort = parseInt(m[1], 10);
        resolve(serverPort);
      }
    });

    serverProcess.stderr.on("data", (c) => console.error("[server]", c.toString().trim()));

    serverProcess.on("error", (err) => {
      if (!settled) { settled = true; reject(err); }
    });
    serverProcess.on("exit", (code) => {
      if (!settled) {
        settled = true;
        reject(new Error(`Backend exited with code ${code} before reporting a port.`));
      }
    });

    // The port line normally arrives in well under a second; a long ceiling
    // only exists so a cold antivirus scan of a fresh binary does not look
    // like a crash.
    setTimeout(() => {
      if (!settled) {
        settled = true;
        reject(new Error("Backend did not report a port within 60 seconds."));
      }
    }, 60000);
  });
}

/** Poll /healthz until the server answers, rather than guessing at a delay. */
function waitForHealth(port, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get(
        { host: "127.0.0.1", port, path: "/healthz", timeout: 2000 },
        (res) => {
          res.resume();
          if (res.statusCode === 200) resolve();
          else retry();
        });
      req.on("error", retry);
      req.on("timeout", () => { req.destroy(); retry(); });
    };
    const retry = () => {
      if (Date.now() > deadline) reject(new Error("Server never became healthy."));
      else setTimeout(attempt, 400);
    };
    attempt();
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1560,
    height: 950,
    minWidth: 1024,
    minHeight: 680,
    backgroundColor: "#0A1017",     // matches the dark theme, so no white flash
    show: false,
    title: "DRISHTI",
    icon: path.join(__dirname, "build", "icon.png"),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow.show());

  // Anything not served by our own backend opens in the system browser rather
  // than hijacking the app window.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith(`http://127.0.0.1:${serverPort}`)) {
      e.preventDefault();
      shell.openExternal(url);
    }
  });

  mainWindow.on("closed", () => { mainWindow = null; });
  mainWindow.loadURL(`http://127.0.0.1:${serverPort}/`);
}

function buildMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: "DRISHTI",
      submenu: [
        { label: "Reload", accelerator: "CmdOrCtrl+R",
          click: () => mainWindow && mainWindow.reload() },
        { label: "Toggle Developer Tools", accelerator: "CmdOrCtrl+Shift+I",
          click: () => mainWindow && mainWindow.webContents.toggleDevTools() },
        { type: "separator" },
        { label: "API documentation",
          click: () => shell.openExternal(`http://127.0.0.1:${serverPort}/docs`) },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    { label: "View", submenu: [
      { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" },
      { type: "separator" }, { role: "togglefullscreen" },
    ] },
  ]));
}

function stopServer() {
  if (!serverProcess) return;
  try {
    if (process.platform === "win32") {
      // SIGTERM does not reliably reach a Windows child; taskkill /T takes the
      // whole tree, which matters because uvicorn may have spawned workers.
      spawn("taskkill", ["/pid", String(serverProcess.pid), "/f", "/t"],
            { windowsHide: true });
    } else {
      serverProcess.kill("SIGTERM");
    }
  } catch (e) {
    console.error("failed to stop backend:", e);
  }
  serverProcess = null;
}

app.whenReady().then(async () => {
  try {
    const port = await startServer();
    await waitForHealth(port);
    buildMenu();
    createWindow();
  } catch (err) {
    dialog.showErrorBox("DRISHTI could not start", String(err.message || err));
    app.quit();
  }
});

app.on("window-all-closed", () => { stopServer(); app.quit(); });
app.on("before-quit", stopServer);
process.on("exit", stopServer);
