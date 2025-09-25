# -*- coding: utf-8 -*-
"""
Test tính năng: Ảnh (storage/input) + PSD template  ->  Result (storage/outputs)

- Quét tất cả file ảnh trong storage/input (jpg/jpeg/png)
- Mỗi ảnh sẽ được ghép vào PSD template (Smart Object giản lược):
    * Lấy composite nền từ PSD
    * Tìm layer placeholder theo tên (ưu tiên): 'design_layer' -> 'design' -> 'design_placeholder'
    * Paste ảnh thiết kế vào bbox của layer đó (giữ tỉ lệ, canh giữa)
    * Nếu có layer text: vẽ tên file (không bắt buộc)
- Xuất JPG chất lượng cao về storage/outputs với hậu tố "__mockup.jpg"

Lưu ý:
- psd-tools không chỉnh sửa trực tiếp Smart Object/text. Đây là bản "dễ triển khai".
- Nếu cần giữ toàn bộ hiệu ứng PSD (warp, styles, linked SO), hãy dùng Photoshop scripting/Photopea API.

Chạy:
    (venv) python test_batch_from_input.py
"""

import os
import re
import io
import glob
import warnings
from pathlib import Path

# Suppress warnings (ảnh lớn và resource lạ trong PSD)
import logging
from PIL import Image, ImageDraw, ImageFont
from psd_tools import PSDImage

# ============== Cấu hình ==============
BASE_DIR       = Path(__file__).resolve().parent
INPUT_DIR      = BASE_DIR / "storage" / "input"
TEMPLATES_DIR  = BASE_DIR / "storage" / "templates"
OUTPUTS_DIR    = BASE_DIR / "storage" / "outputs"

# “Link cũ” để tìm template: <TYPE>/<SIZE>/sda.psd -> templates/sda.psd -> ./sda.psd
TYPE_AO        = "BCNL-IN"
SIZE_AO        = "Adult M"
TEMPLATE_NAME  = "sda.psd"

# Tên layer trong PSD (nếu có)
DESIGN_LAYER_CANDIDATES = ["design_layer", "design", "design_placeholder"]
TEXT_LAYER_CANDIDATES   = ["text_layer", "order", "text"]

# Xuất JPG
JPG_QUALITY    = 95       # 0..100
JPG_SUBSAMPLING= 0        # 4:4:4

# ============ Warnings & Limits ============
Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)
warnings.filterwarnings(
    "ignore",
    message=r"Unknown image resource \d+",
    category=UserWarning,
    module="psd_tools"
)
logging.getLogger("psd_tools").setLevel(logging.ERROR)

# ============== Helpers ==============
INVALID_FS_CHARS = r'[<>:"/\\|?*]'

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def sanitize_filename(name: str, max_len: int = 220) -> str:
    if name is None:
        name = "output"
    s = str(name).replace('\n', ' ').replace('\r', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(INVALID_FS_CHARS, '-', s)
    s = s.replace('/', '-').replace('\\', '-')
    return s[:max_len]

def find_layer_by_name(psd: PSDImage, names):
    names = [n.strip().lower() for n in names]
    def walk(layers):
        for ly in layers:
            nm = getattr(ly, 'name', None)
            if nm and nm.strip().lower() in names:
                return ly
            if hasattr(ly, 'layers') and ly.layers:
                r = walk(ly.layers)
                if r is not None:
                    return r
        return None
    return walk(psd)

def layer_bbox_tuple(layer):
    bbox = getattr(layer, 'bbox', None)
    if not bbox:
        return None
    try:
        return (bbox.x1, bbox.y1, bbox.x2, bbox.y2)
    except Exception:
        try:
            return tuple(bbox)
        except Exception:
            return None

def resize_to_fit(img: Image.Image, target_w: int, target_h: int, keep_aspect=True):
    if keep_aspect:
        im = img.copy()
        im.thumbnail((target_w, target_h), Image.LANCZOS)
        return im
    return img.resize((target_w, target_h), Image.LANCZOS)

def draw_text_in_bbox(base: Image.Image, bbox, text: str, font_path=None, fill=(0,0,0), margin=4):
    if not text or not bbox:
        return
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return
    draw = ImageDraw.Draw(base)
    if font_path and Path(font_path).is_file():
        try:
            size = max(12, min(int(h*0.6), 128))
            font = ImageFont.truetype(font_path, size)
        except Exception:
            font = ImageFont.load_default()
    else:
        font = ImageFont.load_default()
    draw.text((x1 + margin, y1 + margin), text, font=font, fill=fill)

def resolve_template_path(type_ao: str, size: str) -> Path | None:
    candidates = [
        TEMPLATES_DIR / type_ao / size / TEMPLATE_NAME,
        TEMPLATES_DIR / TEMPLATE_NAME,
        BASE_DIR / TEMPLATE_NAME,
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None

def composite_psd_with_design(psd: PSDImage,
                              design_img: Image.Image,
                              order_text: str | None = None) -> Image.Image:
    """
    - Nền: composite từ PSD (RGBA)
    - Paste ảnh thiết kế vào bbox placeholder bằng paste(..., mask=alpha)
    - Vẽ text (tuỳ chọn) vào bbox của layer text
    """
    base = psd.composite().convert("RGBA")  # đảm bảo RGBA

    # CHUẨN BỊ THIẾT KẾ (đảm bảo RGBA và có alpha để làm mask)
    design_src = design_img.convert("RGBA")

    # 1) DESIGN vào bbox placeholder (nếu có)
    dly = find_layer_by_name(psd, DESIGN_LAYER_CANDIDATES)
    if dly:
        bb = layer_bbox_tuple(dly)
        if bb:
            x1, y1, x2, y2 = bb
            tw, th = max(1, x2 - x1), max(1, y2 - y1)
            design_fit = resize_to_fit(design_src, tw, th, keep_aspect=True)

            # toạ độ canh giữa bbox
            ox = x1 + (tw - design_fit.width) // 2
            oy = y1 + (th - design_fit.height) // 2

            # dùng mask alpha để paste
            mask = design_fit.split()[3] if design_fit.mode == "RGBA" else None
            base.paste(design_fit, (ox, oy), mask)
    else:
        # Không có placeholder -> paste giữa canvas
        cw, ch = base.size
        dw, dh = int(cw * 0.9), int(ch * 0.9)
        design_fit = resize_to_fit(design_src, dw, dh, keep_aspect=True)
        ox = (cw - design_fit.width) // 2
        oy = (ch - design_fit.height) // 2
        mask = design_fit.split()[3] if design_fit.mode == "RGBA" else None
        base.paste(design_fit, (ox, oy), mask)

    # 2) TEXT (tuỳ chọn)
    if order_text:
        tly = find_layer_by_name(psd, TEXT_LAYER_CANDIDATES)
        if tly:
            tbb = layer_bbox_tuple(tly)
            # vẽ trực tiếp lên base (RGBA)
            draw_text_in_bbox(base, tbb, order_text, font_path=None, fill=(0,0,0))

    return base.convert("RGB")  # xuất JPG


# ============== Main ==============
def main():
    ensure_dir(OUTPUTS_DIR)

    # 1) Tìm PSD template theo link cũ
    template = resolve_template_path(TYPE_AO, SIZE_AO)
    if not template:
        raise FileNotFoundError(
            f"Không tìm thấy PSD template: "
            f"{TEMPLATES_DIR / TYPE_AO / SIZE_AO / TEMPLATE_NAME} "
            f"hoặc {TEMPLATES_DIR / TEMPLATE_NAME} hoặc {BASE_DIR / TEMPLATE_NAME}"
        )

    psd = PSDImage.open(str(template))

    # 2) Lặp qua ảnh input
    patterns = ["*.jpg", "*.jpeg", "*.png"]
    files = []
    for pat in patterns:
        files.extend(glob.glob(str(INPUT_DIR / pat)))

    if not files:
        print(f"[INFO] Không có ảnh trong: {INPUT_DIR}")
        return

    print(f"[INFO] Dùng template: {template}")
    print(f"[INFO] Tìm thấy {len(files)} ảnh, bắt đầu xử lý...")

    for f in files:
        try:
            p = Path(f)
            stem = sanitize_filename(p.stem)
            design_img = Image.open(p).convert("RGBA")

            # order_text đặt theo tên file
            order_text = f"{TYPE_AO}-Đen-{SIZE_AO}-{stem}"

            result = composite_psd_with_design(psd, design_img, order_text=order_text)

            out_name = f"{stem}__mockup.jpg"
            out_path = OUTPUTS_DIR / out_name
            result.save(out_path, format="JPEG", quality=JPG_QUALITY, subsampling=JPG_SUBSAMPLING)

            print(f"[OK] {p.name} -> {out_path.name}")
        except Exception as e:
            print(f"[ERR] {f}: {e}")

    print("[DONE] Hoàn tất.")

if __name__ == "__main__":
    main()
