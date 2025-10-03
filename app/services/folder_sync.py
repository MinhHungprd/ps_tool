# app/services/folder_sync.py
import os
from pathlib import Path
from typing import List, Tuple, Optional
from datetime import datetime

# Nguồn xuất local mặc định (giữ nguyên cách của dự án cũ)
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "storage/outputs"))

def _today_folder_name() -> str:
    # DD_MM_YY, giống với sync Drive trước đây
    return datetime.now().strftime("%d_%m_%y")

def ensure_dest_root(dest_root: str) -> Path:
    """
    Tạo thư mục đích nếu chưa có. Trả về Path tuyệt đối.
    """
    p = Path(dest_root).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p

def move_outputs_to_folder(dest_root: str, delete_local: bool = True) -> List[Tuple[str, Optional[str]]]:
    """
    Di chuyển toàn bộ *.jpg trong OUTPUT_DIR sang dest_root/DD_MM_YY/.
    Trả về list (filename, dest_path) — dest_path=None nếu thất bại.
    Không đụng tới .ok/.err.

    Cách tổ chức: <dest_root>/<DD_MM_YY>/<tên_file>.jpg
    """
    results: List[Tuple[str, Optional[str]]] = []

    if not OUTPUT_DIR.is_dir():
        return results

    files = [
        p for p in OUTPUT_DIR.iterdir()
        if p.is_file()
        and p.suffix.lower() == ".jpg"
        and not p.name.endswith((".ok", ".err"))
    ]
    if not files:
        return results

    root = ensure_dest_root(dest_root)
    day_dir = root / _today_folder_name()
    day_dir.mkdir(parents=True, exist_ok=True)

    for p in files:
        try:
            dst = day_dir / p.name
            if dst.exists():
                # Nếu đã có, bỏ qua (như logic "skip if exists" trước đây)
                results.append((p.name, str(dst)))
                if delete_local:
                    try:
                        p.unlink()
                    except Exception:
                        pass
                continue

            # Di chuyển (ưu tiên rename nhanh, fallback copy)
            try:
                p.replace(dst)
            except Exception:
                import shutil
                shutil.copy2(p, dst)
                if delete_local:
                    try:
                        p.unlink()
                    except Exception:
                        pass

            results.append((p.name, str(dst)))
        except Exception:
            results.append((p.name, None))

    return results
