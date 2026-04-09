const { app, BrowserWindow, shell } = require("electron")
const path = require("path")
const { spawn, execSync } = require("child_process")

let mainWindow
let serverProcess

const PORT = 3333

function findNode() {
  // Use system node (not Electron's), needed for native modules like better-sqlite3
  try {
    const nodePath = execSync("which node", { encoding: "utf-8" }).trim()
    if (nodePath) return nodePath
  } catch {}
  // Fallbacks
  if (require("fs").existsSync("/opt/homebrew/bin/node")) return "/opt/homebrew/bin/node"
  if (require("fs").existsSync("/usr/local/bin/node")) return "/usr/local/bin/node"
  return "node"
}

function startServer() {
  const projectDir = path.join(__dirname, "..")
  const nodeBin = findNode()
  const tsxBin = path.join(projectDir, "node_modules", ".bin", "tsx")

  console.log(`Using node: ${nodeBin}`)
  console.log(`Using tsx: ${tsxBin}`)

  serverProcess = spawn(tsxBin, ["server/index.ts"], {
    cwd: projectDir,
    env: { ...process.env, PORT: String(PORT), NODE_ENV: "production", PATH: path.dirname(nodeBin) + ":" + process.env.PATH },
    stdio: ["ignore", "pipe", "pipe"],
  })

  serverProcess.stdout.on("data", (d) => console.log("[server]", d.toString().trim()))
  serverProcess.stderr.on("data", (d) => console.error("[server]", d.toString().trim()))

  serverProcess.on("error", (e) => console.error("[server] spawn error:", e))

  return new Promise((resolve) => {
    serverProcess.stdout.on("data", (d) => {
      if (d.toString().includes("Server running")) resolve()
    })
    setTimeout(resolve, 8000)
  })
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    titleBarStyle: "default",
    backgroundColor: "#080b14",
    title: "Pokemon Tracker",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  })

  mainWindow.loadURL(`http://localhost:${PORT}`)

  // Open Cardmarket links in default browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.includes("cardmarket.com")) {
      shell.openExternal(url)
      return { action: "deny" }
    }
    return { action: "allow" }
  })

  mainWindow.on("closed", () => {
    mainWindow = null
  })
}

app.whenReady().then(async () => {
  console.log("Starting server...")
  await startServer()
  console.log("Server ready, opening window...")
  createWindow()
})

app.on("window-all-closed", () => {
  if (serverProcess) serverProcess.kill()
  app.quit()
})

app.on("before-quit", () => {
  if (serverProcess) serverProcess.kill()
})

app.on("activate", () => {
  if (!mainWindow) createWindow()
})
