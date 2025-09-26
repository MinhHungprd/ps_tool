# add_text.py
import os
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont

BASE_DIR   = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "storage" / "outputs"
RED = (255, 0, 0)

# ======= HẰNG SỐ SỬA TẠI ĐÂY =======
BOX_RATIO   = (92.97, 40.79, 99.54, 53.28)   # (left%, top%, right%, bottom%)
INSET_RATIO = 0.04                        # thu nhỏ 4% mỗi cạnh để giữ viền
ORIENTATION = "ccw"                       # "ccw" = xoay 90° ngược chiều kim; "cw" = cùng chiều

def load_bold_font(size: int):
    for p in [r"C:\Windows\Fonts\timesbd.ttf",
              r"C:\Windows\Fonts\arialbd.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.isfile(p):
            try: return ImageFont.truetype(p, size=size)
            except: pass
    return ImageFont.load_default()

def ratio_to_box(w, h, ratio):
    l,t,r,b = ratio
    return (int(round(l/100*w)), int(round(t/100*h)),
            int(round(r/100*w)), int(round(b/100*h)))

def inner_box(x0,y0,x1,y1, ratio):
    w, h = (x1-x0+1), (y1-y0+1)
    dx, dy = max(2, int(w*ratio)), max(2, int(h*ratio))
    return (x0+dx, y0+dy, x1-dx, y1-dy)

def estimate_bg(rgb, box):
    x0,y0,x1,y1 = box
    roi = rgb[y0:y1+1, x0:x1+1]
    # loại đen & đỏ để lấy median nền (thường ~trắng)
    gray = (0.299*roi[...,0] + 0.587*roi[...,1] + 0.114*roi[...,2]).astype(np.uint8)
    black = gray < 35
    r,g,b = roi[...,0], roi[...,1], roi[...,2]
    red = (r > 170) & (g < 120) & (b < 120) & ((r - np.maximum(g,b)) > 40)
    use = ~(black | red)
    sample = roi[use]
    if sample.size == 0: return (245,245,245)
    med = np.median(sample.reshape(-1,3), axis=0)
    return tuple(int(v) for v in med)

def fit_vertical_font(text, box_w, box_h):
    base = load_bold_font(32)
    lo, hi = 8, max(12, int(box_h*0.95))
    best = base.font_variant(size=lo)
    while lo <= hi:
        mid = (lo+hi)//2
        f = base.font_variant(size=mid)
        tmp = Image.new("L", (1,1)); d = ImageDraw.Draw(tmp)
        w,h = d.textbbox((0,0), text, font=f)[2:]
        txt = Image.new("L", (w,h), 0); ImageDraw.Draw(txt).text((0,0), text, fill=255, font=f)
        rot = txt.rotate(-90 if ORIENTATION=="ccw" else 90, expand=True)
        tw, th = rot.size
        if tw <= box_w*0.92 and th <= box_h*0.92:
            best = f; lo = mid+1
        else:
            hi = mid-1
    return best

def draw_text_keep_border(img, outer_box, text):
    x0,y0,x1,y1 = outer_box
    # chỉ fill phần trong để giữ viền
    ix0,iy0,ix1,iy1 = inner_box(x0,y0,x1,y1, INSET_RATIO)

    rgb = np.asarray(img.convert("RGB"))
    bg  = estimate_bg(rgb, (ix0,iy0,ix1,iy1))
    d = ImageDraw.Draw(img)
    d.rectangle([ix0,iy0,ix1,iy1], fill=bg)

    box_w, box_h = (ix1-ix0+1, iy1-iy0+1)
    font = fit_vertical_font(text, box_w, box_h)

    tmp = Image.new("L", (1,1)); draw = ImageDraw.Draw(tmp)
    w,h = draw.textbbox((0,0), text, font=font)[2:]
    txt = Image.new("L", (w,h), 0); ImageDraw.Draw(txt).text((0,0), text, fill=255, font=font)
    rot = txt.rotate(-90 if ORIENTATION=="ccw" else 90, expand=True)
    tw, th = rot.size
    ox = ix0 + (box_w - tw)//2
    oy = iy0 + (box_h - th)//2
    colored = Image.new("RGBA", rot.size, (0,0,0,0))
    ImageDraw.Draw(colored).bitmap((0,0), rot, fill=(255,0,0,255))
    img.paste(colored, (ox,oy), colored)

def process_file(filename: str):
    img_path = OUTPUT_DIR / filename
    if not img_path.exists(): raise FileNotFoundError(img_path)
    im = Image.open(img_path).convert("RGB")
    W,H = im.size
    outer = ratio_to_box(W,H,BOX_RATIO)
    text = Path(filename).stem
    draw_text_keep_border(im, outer, text)
    im.save(img_path, quality=95)
    print("OK:", img_path)
    # debug toạ độ pixel thực tế
    print("BOX(px):", outer)

if __name__ == "__main__":
    import sys
    if len(sys.argv)<2:
        print("Usage: python add_text.py <filename>")
        raise SystemExit(1)
    process_file(sys.argv[1])
