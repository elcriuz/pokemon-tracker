#!/usr/bin/env node
/**
 * Simple GitHub Webhook receiver for auto-deployment.
 * Listens on port 9000, pulls latest code + rebuilds on push events.
 *
 * Setup: node deploy/webhook.js
 * GitHub Webhook URL: http://<container-ip>:9000/deploy
 * Secret: set WEBHOOK_SECRET env var
 */
import { createServer } from "http"
import { execSync } from "child_process"
import { createHmac } from "crypto"

const PORT = 9000
const PROJECT_DIR = "/opt/pokemon-tracker"
const SECRET = process.env.WEBHOOK_SECRET || "pokemon-tracker-deploy"

function verifySignature(payload, signature) {
  if (!signature) return false
  const hmac = createHmac("sha256", SECRET)
  hmac.update(payload)
  const expected = "sha256=" + hmac.digest("hex")
  return signature === expected
}

function deploy() {
  console.log(`[${new Date().toISOString()}] Deploying...`)
  try {
    const opts = { cwd: PROJECT_DIR, stdio: "pipe", timeout: 120_000 }

    console.log("  git pull...")
    execSync("git pull origin main", opts)

    console.log("  pnpm install...")
    execSync("pnpm install", opts)

    console.log("  vite build...")
    execSync("npx vite build", opts)

    console.log("  restart service...")
    execSync("systemctl restart pokemon-tracker", opts)

    console.log("  DONE!")
    return true
  } catch (e) {
    console.error("  DEPLOY FAILED:", e.message)
    return false
  }
}

const server = createServer((req, res) => {
  if (req.method === "POST" && req.url === "/deploy") {
    let body = ""
    req.on("data", (chunk) => (body += chunk))
    req.on("end", () => {
      const sig = req.headers["x-hub-signature-256"]
      if (!verifySignature(body, sig)) {
        console.log("Invalid signature, ignoring")
        res.writeHead(401)
        res.end("Unauthorized")
        return
      }

      try {
        const payload = JSON.parse(body)
        if (payload.ref === "refs/heads/main") {
          console.log(`Push to main by ${payload.pusher?.name || "unknown"}`)
          res.writeHead(200)
          res.end("Deploying...")
          deploy()
        } else {
          res.writeHead(200)
          res.end("Not main branch, skipping")
        }
      } catch {
        res.writeHead(400)
        res.end("Invalid payload")
      }
    })
  } else {
    res.writeHead(200)
    res.end("OK")
  }
})

server.listen(PORT, () => {
  console.log(`Webhook listener running on port ${PORT}`)
  console.log(`URL: http://0.0.0.0:${PORT}/deploy`)
})
