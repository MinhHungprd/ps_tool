import os
import re
import tempfile
import pandas as pd
from typing import Optional, List, Dict

BASE_DIR      = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATES_DIR = os.path.join(BASE_DIR, "storage", "templates")
OUTPUTS_DIR   = os.path.join(BASE_DIR, "storage", "outputs")
TMP_DIR       = os.path.join(BASE_DIR, "storage", "tmp")
DEFAULT_EXCEL = os.path.join(BASE_DIR, "data.xlsx")

from app.services.google_drive import download_from_share_link  # tải file thiết kế

# ---------- Utils ----------
INVALID_FS_CHARS = r'[<>:"/\\|?*\n\r]'

def _ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

def _sanitize_filename(name: str, max_len: int = 220) -> str:
    s = str(name)
    s = re.sub(INVALID_FS_CHARS, "-", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]

def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df

def _col_by_letter(df: pd.DataFrame, letter: str) -> Optional[str]:
    idx = ord(letter.upper()) - ord("A")
    return df.columns[idx] if 0 <= idx < len(df.columns) else None

def _pick_series(df: pd.DataFrame, prefer_letter: str, fallbacks: List[str]) -> Optional[str]:
    col = _col_by_letter(df, prefer_letter)
    if col is not None:
        return col
    norm = {c.lower(): c for c in df.columns}
    for f in fallbacks:
        if f.lower() in norm:
            return norm[f.lower()]
    return None

def _right_without_first_char(s: str) -> str:
    s = "" if s is None else str(s)
    return s[1:] if len(s) > 0 else ""

def _compute_counts_for_O(df: pd.DataFrame, o_col: str, count_range_max: int = 1000):
    work = df.copy()
    o_series = work[o_col].astype(str)
    # Tổng xuất hiện trong dải O2:O1000 (nếu file ngắn hơn thì lấy hết)
    upto = min(len(work), max(1, count_range_max - 1))  # từ dòng 2 Excel → index 0 pandas
    totals = o_series.iloc[:upto].value_counts()
    work["_o_key"]   = o_series
    work["_o_total"] = work["_o_key"].map(totals).fillna(0).astype(int)
    work["_o_cum"]   = work.groupby("_o_key").cumcount() + 1
    return work[["_o_total", "_o_cum"]]

def _resolve_template_path(loai_ao: str, size_text: str) -> Optional[str]:
    cands = [
        os.path.join(TEMPLATES_DIR, str(loai_ao).strip(), str(size_text).strip(), "sda.psd"),
        os.path.join(TEMPLATES_DIR, "sda.psd"),
        os.path.join(BASE_DIR, "sda.psd"),
    ]
    for p in cands:
        if os.path.isfile(p):
            return p
    return None

# ---------- Core ----------
def process_order_from_excel(excel_path: Optional[str] = None,
                             sheet: Optional[str] = None,
                             limit: int = 1,
                             count_range_max: int = 1000) -> List[Dict]:
    """
    Chỉ chuẩn bị dữ liệu cho psd_handler:
      - Đọc Excel
      - Tính output_name theo công thức:
        J & "-" & N & "-" & M & "-" & RIGHT(O, LEN(O)-1) & "-" & COUNTIF($O$2:O2,O2) & "/" & COUNTIF($O$2:$O$1000,O2)
      - Tải file thiết kế → ghi file tạm (image_input)
      - Xác định path_sda = TEMPLATES_DIR/J/M/sda.psd
      - Trả về danh sách jobs (chưa render)

    Mỗi job:
      {
        "row": int,
        "status": "READY" | "SKIP" | "ERROR",
        "reason": "...",           # nếu SKIP/ERROR
        "display_name": "...",     # tên hiển thị đúng công thức (có '/')
        "output_name": "...",      # đã sanitize cho tên file
        "psd_path": ".../sda.psd",
        "img_path": ".../tmp_xxx.jpg",   # file tạm đã tải xuống
        "out_path": ".../outputs/<output_name>.jpg",
        "tmp_img": True            # để handler tự dọn file tạm
      }
    """
    excel = excel_path or DEFAULT_EXCEL
    if not os.path.isfile(excel):
        raise FileNotFoundError(f"Không thấy Excel: {excel}")

    df = pd.read_excel(excel, sheet_name=sheet) if sheet else pd.read_excel(excel)
    df = _norm_cols(df)

    # Map cột J/N/M/O
    j_col = _pick_series(df, "J", ["Loại áo", "Loai ao", "Type"])
    n_col = _pick_series(df, "N", ["Màu", "Mau", "Color"])
    m_col = _pick_series(df, "M", ["Size", "Kích cỡ", "Kich co"])
    o_col = _pick_series(df, "O", ["Order ID", "Mã đơn", "Mã đơn hàng"])
    if not all([j_col, n_col, m_col, o_col]):
        miss = [k for k,v in {"J":j_col,"N":n_col,"M":m_col,"O":o_col}.items() if v is None]
        raise KeyError(f"Không xác định được các cột: {', '.join(miss)} (J/N/M/O).")

    # Cột link “File thiết kế”
    link_col = None
    for cand in ["File thiết kế", "File thiet ke", "Link file thiết kế", "Link"]:
        c = next((c for c in df.columns if c.lower() == cand.lower()), None)
        if c:
            link_col = c
            break
    # Fallback đoán theo chữ cái nếu không có tiêu đề mong muốn
    if link_col is None:
        link_col = _pick_series(df, "L", ["File thiết kế", "File thiet ke", "Link"]) or \
                   _pick_series(df, "K", ["File thiết kế", "File thiet ke", "Link"])

    # Đếm cho O (COUNTIF)
    counts = _compute_counts_for_O(df, o_col, count_range_max=count_range_max)

    _ensure_dir(OUTPUTS_DIR)
    _ensure_dir(TMP_DIR)

    rows = df.head(limit) if (limit and limit > 0) else df
    jobs: List[Dict] = []

    for idx, row in rows.iterrows():
        try:
            jv = str(row[j_col]).strip()
            nv = str(row[n_col]).strip()
            mv = str(row[m_col]).strip()
            ov = str(row[o_col]).strip()

            # build output name (display) theo công thức Excel
            display_name = f"{jv}-{nv}-{mv}-{_right_without_first_char(ov)}-{int(counts.loc[idx, '_o_cum'])}/{int(counts.loc[idx, '_o_total'])}"
            output_name  = _sanitize_filename(display_name)   # filename an toàn
            out_path     = os.path.join(OUTPUTS_DIR, f"{output_name}.jpg")

            # resolve sda.psd theo J/M
            path_sda = _resolve_template_path(jv, mv)
            if not path_sda:
                jobs.append({
                    "row": int(idx), "status": "ERROR",
                    "reason": f"Không tìm thấy sda.psd cho '{jv}/{mv}'",
                    "display_name": display_name, "output_name": output_name
                })
                continue

            # lấy link file thiết kế
            if not link_col or pd.isna(row[link_col]):
                jobs.append({
                    "row": int(idx), "status": "SKIP",
                    "reason": "Thiếu cột hoặc giá trị 'File thiết kế'",
                    "display_name": display_name, "output_name": output_name
                })
                continue
            design_link = str(row[link_col]).strip()
            if not design_link or design_link.lower() == "nan":
                jobs.append({
                    "row": int(idx), "status": "SKIP",
                    "reason": "Giá trị 'File thiết kế' rỗng",
                    "display_name": display_name, "output_name": output_name
                })
                continue

            # tải bytes ảnh và ghi file tạm
            data = download_from_share_link(design_link)
            # đoán đuôi
            try:
                import imghdr
                ext = imghdr.what(None, h=data) or "jpg"
            except Exception:
                ext = "jpg"
            # ghi vào storage/tmp
            fd, tmp_bin = tempfile.mkstemp(prefix="design_", suffix=f".{ext}", dir=TMP_DIR)
            os.close(fd)
            with open(tmp_bin, "wb") as f:
                f.write(data)

            jobs.append({
                "row": int(idx),
                "status": "READY",
                "display_name": display_name,
                "output_name": output_name,
                "psd_path": path_sda,
                "img_path": tmp_bin,
                "out_path": out_path,
                "tmp_img": True,      # để handler dọn file tạm
            })

        except Exception as e:
            jobs.append({
                "row": int(idx), "status": "ERROR",
                "reason": str(e)
            })

    return jobs
