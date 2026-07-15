"""Warm MobileCLIP retrieval microservice (runs in cardvision-venv on Container 103).
Loads the model + index once, then answers POST /candidates {image_b64, k} with the
top-K catalog candidates (metadata + cosine score). No OpenAI, no thumbnails — the bot
does OCR cross-check / confirm. Also GET /health."""
import sys, json, base64, tempfile, os
sys.path.insert(0, "/opt/pokemon-tracker/cardvision")
import numpy as np
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PIL import Image
from common import get_model, embed_pils, crop_card

IDX = "/opt/pokemon-tracker/data/cardindex"
PORT = int(os.environ.get("CARDVISION_PORT", "8099"))

EMBS = np.load(os.path.join(IDX, "embeddings.npy"))
META = json.load(open(os.path.join(IDX, "meta.json")))
get_model()
embed_pils([Image.new("RGB", (256, 256))])  # warm
sys.stderr.write(f"cardvision ready: {EMBS.shape[0]} cards, port {PORT}\n"); sys.stderr.flush()


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok", "cards": EMBS.shape[0]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/candidates":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n))
            k = int(body.get("k", 6))
            img = base64.b64decode(body["image_b64"])
        except Exception as e:
            return self._send(400, {"error": f"bad request: {e}"})
        path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as t:
                t.write(img); path = t.name
            crop, mode = crop_card(path)
            q = embed_pils([crop])[0]
            sims = EMBS @ q
            top = np.argsort(-sims)[:k]
            cands = [{"name": META[i]["name"], "set_id": META[i]["set_id"],
                      "number": META[i]["number"], "lang": META[i]["lang"],
                      "printedTotal": META[i].get("printedTotal"), "score": round(float(sims[i]), 4)}
                     for i in top]
            self._send(200, {"crop_mode": mode, "candidates": cands})
        except Exception as e:
            self._send(500, {"error": str(e)})
        finally:
            if path and os.path.exists(path):
                os.unlink(path)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
