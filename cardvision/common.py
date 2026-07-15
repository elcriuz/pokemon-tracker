"""Shared: MobileCLIP-S2 embedding (MPS) + OpenCV card crop."""
import numpy as np, cv2
from PIL import Image
import torch, open_clip

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
_MODEL = None
_PRE = None


def get_model():
    global _MODEL, _PRE
    if _MODEL is None:
        m, _, pre = open_clip.create_model_and_transforms("MobileCLIP-S2", pretrained="datacompdr")
        m = m.to(DEVICE).eval()
        _MODEL, _PRE = m, pre
    return _MODEL, _PRE


@torch.no_grad()
def embed_pils(pils, batch=64):
    """List[PIL] -> (N, D) float32, L2-normalized."""
    model, pre = get_model()
    out = []
    for i in range(0, len(pils), batch):
        chunk = pils[i:i + batch]
        t = torch.stack([pre(p) for p in chunk]).to(DEVICE)
        f = model.encode_image(t).float()
        f = f / f.norm(dim=-1, keepdim=True)
        out.append(f.cpu().numpy())
    return np.concatenate(out, axis=0).astype("float32")


def _order(pts):
    r = np.zeros((4, 2), dtype="float32")
    s = pts.sum(1); r[0] = pts[np.argmin(s)]; r[2] = pts[np.argmax(s)]
    d = np.diff(pts, axis=1); r[1] = pts[np.argmin(d)]; r[3] = pts[np.argmax(d)]
    return r


def _rect_candidates(mask, area, tag):
    """Yield (af, box4, tag) for VALID card-like rectangles in a binary mask —
    card aspect (~0.714), rectangular, and covering a substantial area. Returns
    area-fraction so the caller can prefer the biggest (the whole card, not a
    card-shaped sub-region like the art window)."""
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:6]:
        a = cv2.contourArea(c)
        af = a / area
        if af < 0.30 or af > 0.95:                 # card must be a large chunk of the photo
            continue
        rect = cv2.minAreaRect(c)
        rw, rh = rect[1]
        if rw < 2 or rh < 2:
            continue
        ar = min(rw, rh) / max(rw, rh)
        fill = a / (rw * rh)
        if abs(ar - 0.714) <= 0.16 and fill > 0.80:  # card-shaped and rectangular
            out.append((af, cv2.boxPoints(rect), tag))
    return out


def crop_card(path, out=(384, 536)):
    """Detect the card by scoring rectangle candidates from several masks
    (edges + grayscale-Otsu + saturation-Otsu). Saturation isolates the colorful
    card from wood/sleeve/slab-plastic backgrounds. Falls back to center crop."""
    img = cv2.imread(str(path))
    if img is None:
        return Image.open(path).convert("RGB"), "read-fail"
    h, w = img.shape[:2]
    sc = 1200.0 / max(h, w)
    small = cv2.resize(img, (int(w * sc), int(h * sc)))
    area = small.shape[0] * small.shape[1]
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    graf = cv2.bilateralFilter(gray, 9, 75, 75)
    sat = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[:, :, 1]

    cands = []
    edged = cv2.Canny(graf, 30, 200)
    cands += _rect_candidates(edged, area, "edge")
    _, gm = cv2.threshold(graf, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    cands += _rect_candidates(gm, area, "gray")
    cands += _rect_candidates(255 - gm, area, "gray")
    _, sm = cv2.threshold(sat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    cands += _rect_candidates(sm, area, "sat")

    W, H = out
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype="float32")
    if cands:
        af, box, tag = max(cands, key=lambda t: t[0])   # biggest valid card wins
        M = cv2.getPerspectiveTransform(_order((box / sc).astype("float32")), dst)
        warp = cv2.warpPerspective(img, M, (W, H))
        return Image.fromarray(cv2.cvtColor(warp, cv2.COLOR_BGR2RGB)), tag
    cw, ch = int(w * 0.72), int(h * 0.74)
    x, y = (w - cw) // 2, (h - ch) // 2
    crop = img[y:y + ch, x:x + cw]
    return Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)), "center-fallback"
