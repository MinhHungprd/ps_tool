import os
import io
import re
import json
import warnings
import requests
import pandas as pd
from psd_tools import PSDImage
from PIL import Image, ImageDraw, ImageFont

# ====== Cấu hình đường dẫn ======
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, 'storage', 'templates')
OUTPUTS_DIR   = os.path.join(BASE_DIR, 'storage', 'outputs')

# ====== An toàn ảnh lớn (local) ======
# Pillow cảnh báo "DecompressionBombWarning" với ảnh > ~85MP.
# Chạy local, dữ liệu tự kiểm soát → tắt giới hạn.
Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter('ignore', Image.DecompressionBombWarning)

# ====== Helpers ======
INVALID_FS_CHARS = r'[<>:"/\\|?*]'

def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

def sanitize_filename(name: str, max_len: int = 220) -> str:
    if name is None:
        name = "output"
    # Chuẩn hoá whitespace, bỏ xuống dòng, thay ký tự cấm
    s = str(name).replace('\n', ' ').replace('\r', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(INVALID_FS_CHARS, '-', s)
    # Một số Order ID có "1/3" → thay bằng "1-3"
    s = s.replace('/', '-').replace('\\', '-')
    # Cắt bớt nếu quá dài
    return s[:max_len]

def normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    # Hạ thấp + strip header để tra cứu ổn định (kể cả có khoảng trắng cuối)
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    return df

def pick(df_row: pd.Series, candidates):
    """Lấy giá trị theo danh sách key dự phòng (đã normalize header)."""
    for k in candidates:
        if k in df_row and pd.notna(df_row[k]):
            return str(df_row[k])
    return None

def extract_drive_file_id(link: str) -> str | None:
    if not link:
        return None
    # Hỗ trợ các dạng link phổ biến
    m = re.search(r'/d/([A-Za-z0-9_-]+)', link)
    if m: return m.group(1)
    m = re.search(r'open\?id=([A-Za-z0-9_-]+)', link)
    if m: return m.group(1)
    m = re.search(r'uc\?(?:export=download&)?id=([A-Za-z0-9_-]+)', link)
    if m: return m.group(1)
    return None

def download_image(link: str, timeout=60) -> bytes:
    """Tải ảnh từ link; nếu là Google Drive share -> chuyển sang uc?export=download."""
    if not link:
        raise ValueError("Empty design link")
    fid = extract_drive_file_id(link)
    url = link
    headers = {"User-Agent": "Mozilla/5.0"}
    if fid:
        url = f"https://drive.google.com/uc?export=download&id={fid}"
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.content

def open_image_bytes(b: bytes) -> Image.Image:
    return Image.open(io.BytesIO(b)).convert("RGBA")

def find_layer_by_name(psd: PSDImage, name: str):
    # Duyệt đệ quy toàn bộ layer để tìm theo tên (case-insensitive)
    lname = name.strip().lower()
    def walk(layers):
        for ly in layers:
            if getattr(ly, 'name', None) and ly.name.strip().lower() == lname:
                return ly
            if hasattr(ly, 'layers') and ly.layers:
                r = walk(ly.layers)
                if r is not None:
                    return r
        return None
    return walk(psd)

def layer_bbox_tuple(layer):
    # psd-tools Rectangle => (x1,y1,x2,y2)
    bbox = getattr(layer, 'bbox', None) or getattr(layer, 'bbox', None)
    if not bbox:
        return None
    # Một số version: layer.bbox là BBox(x1,y1,x2,y2) hoặc layer.bbox._replace
    try:
        return (bbox.x1, bbox.y1, bbox.x2, bbox.y2)
    except Exception:
        try:
            # tuple-like
            return tuple(bbox)
        except Exception:
            return None

def resize_to_fit(img: Image.Image, target_w: int, target_h: int, keep_aspect=True):
    if keep_aspect:
        img = img.copy()
        img.thumbnail((target_w, target_h), Image.LANCZOS)
        # Nếu muốn fill tràn bbox: có thể crop/letterbox tuỳ template
        return img
    else:
        return img.resize((target_w, target_h), Image.LANCZOS)

def draw_text_in_bbox(base: Image.Image, bbox, text: str, font_path=None, fill=(0,0,0), margin=4):
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0 or not text:
        return
    draw = ImageDraw.Draw(base)
    # Font: nếu có font TTF thì dùng, không có thì dùng default
    if font_path and os.path.isfile(font_path):
        try:
            # Ướm cỡ chữ tương đối với bbox
            size = max(12, min(int(h*0.6), 128))
            font = ImageFont.truetype(font_path, size)
        except Exception:
            font = ImageFont.load_default()
    else:
        font = ImageFont.load_default()

    # Vẽ text trái-trên trong bbox, chừa margin
    tx, ty = x1 + margin, y1 + margin
    draw.text((tx, ty), text, font=font, fill=fill)

def composite_psd_with_design(psd: PSDImage, design_img: Image.Image,
                              design_layer_name='design_layer',
                              text_layer_name='text_layer',
                              order_text: str | None = None) -> Image.Image:
    """
    Cách làm 'dễ triển khai' với psd-tools:
    - Lấy ảnh nền composite từ PSD.
    - Tìm bbox layer placeholder (design_layer) → paste ảnh design đã resize đúng khung.
    - Tìm bbox layer text (text_layer) → vẽ text order.
    """
    base = psd.composite()  # RGBA
    # 1) Paste thiết kế vào bbox của layer placeholder
    dly = find_layer_by_name(psd, design_layer_name) or find_layer_by_name(psd, 'design') \
          or find_layer_by_name(psd, 'design_placeholder')
    if dly:
        bb = layer_bbox_tuple(dly)
        if bb:
            x1, y1, x2, y2 = bb
            tw, th = max(1, x2 - x1), max(1, y2 - y1)
            design_fit = resize_to_fit(design_img, tw, th, keep_aspect=True)
            # canh giữa trong bbox
            ox = x1 + (tw - design_fit.width)//2
            oy = y1 + (th - design_fit.height)//2
            base.alpha_composite(design_fit, dest=(ox, oy))
    # 2) Vẽ text order vào bbox của layer text (nếu có)
    if order_text:
        tly = find_layer_by_name(psd, text_layer_name) or find_layer_by_name(psd, 'order') \
              or find_layer_by_name(psd, 'text')
        if tly:
            tbb = layer_bbox_tuple(tly)
            if tbb:
                draw_text_in_bbox(base, tbb, order_text, font_path=None, fill=(0,0,0))
    return base.convert("RGB")  # Xuất JPG

def resolve_template_path(type_ao: str, size: str) -> str | None:
    # Ưu tiên: storage/templates/<type>/<size>/sda.psd
    cand = [
        os.path.join(TEMPLATES_DIR, type_ao, size, 'sda.psd'),
        os.path.join(TEMPLATES_DIR, 'sda.psd'),
        os.path.join(BASE_DIR, 'sda.psd'),
    ]
    for p in cand:
        if os.path.isfile(p):
            return p
    return None

# ====== Luồng chính ======
def process_one_order(order_id: str, type_ao: str, size: str, design_link: str) -> str:
    # Chuẩn hoá dữ liệu
    type_ao = (type_ao or '').strip()
    size    = (size or '').replace('\n', ' ').strip()
    order_id_s = sanitize_filename(order_id)
    # Output name theo yêu cầu: <TYPE>-Đen-Adult <SIZE>-<ORDER_ID>.jpg
    # Tránh lặp "Adult" 2 lần nếu size đã chứa "Adult"
    size_part = size if re.search(r'\badult\b', size, flags=re.I) else f'Adult {size}'.strip()
    output_name = f"{type_ao}-Đen-{size_part}-{order_id_s}.jpg"
    output_name = sanitize_filename(output_name)
    output_path = os.path.join(OUTPUTS_DIR, output_name)
    ensure_dir(os.path.dirname(output_path))

    # Tải ảnh thiết kế
    design_bytes = download_image(design_link)
    design_img = open_image_bytes(design_bytes)

    # Mở PSD template
    template_path = resolve_template_path(type_ao, size)
    if not template_path:
        raise FileNotFoundError(f"Không tìm thấy PSD template cho type='{type_ao}', size='{size}'")

    psd = PSDImage.open(template_path)

    # Ghép theo cách 'dễ triển khai'
    composite = composite_psd_with_design(
        psd,
        design_img,
        design_layer_name='design_layer',
        text_layer_name='text_layer',
        order_text=f"{type_ao}-Đen-{size_part}-{order_id}"
    )

    # Lưu JPG chất lượng cao (4:4:4)
    composite.save(output_path, format='JPEG', quality=95, subsampling=0)
    return output_path

def test_psd_processing_with_sheet(excel_path: str, sheet_name: str = None):
    ensure_dir(OUTPUTS_DIR)
    df = pd.read_excel(excel_path, sheet_name=sheet_name) if sheet_name else pd.read_excel(excel_path)
    df = normalize_headers(df)
    if df.empty:
        print("Excel rỗng.")
        return

    # Gợi ý key theo yêu cầu PDF / file mẫu của bạn
    key_order   = ['order id', 'mã đơn', 'mã đơn hàng']
    key_link    = ['file thiết kế', 'file thiet ke', 'link', 'link file thiết kế']
    key_type    = ['loại áo', 'loai ao', 'type']
    key_size    = ['size', 'kích cỡ', 'kich co']

    # Lấy bản ghi đầu (có thể lặp nhiều dòng nếu cần)
    row = df.iloc[0]
    order_id   = pick(row, key_order)
    design_link= pick(row, key_link)
    type_ao    = pick(row, key_type)
    size       = pick(row, key_size)

    missing = [k for k,v in [('Order ID',order_id),('File thiết kế',design_link),('Loại áo',type_ao),('Size',size)] if not v]
    if missing:
        print(f"Bỏ qua hàng do thiếu cột: {', '.join(missing)}")
        return

    out = process_one_order(order_id, type_ao, size, design_link)
    print(f"Đã xuất file: {out}")

if __name__ == "__main__":
    # Ví dụ: storage/data.xlsx, sheet 'data'
    excel_path = os.path.join(BASE_DIR, "storage", "data.xlsx")
    sheet_name = "data"
    test_psd_processing_with_sheet(excel_path, sheet_name)
