# app/services/drive_sync.py
import os, json, time
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from datetime import datetime

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# (optional) .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# ===== ENV & đường dẫn =====
# File token chung (tương thích ngược)
TOKEN_DB = os.environ.get("DRIVE_TOKEN_FILE", "storage/driveSession/drive_tokens.json")
# Thư mục token per-user (mới)
TOKEN_DIR = Path(os.environ.get("DRIVE_TOKEN_DIR", "storage/driveSession"))
TOKEN_DIR.mkdir(parents=True, exist_ok=True)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
DRIVE_FOLDER_NAME = os.environ.get("DRIVE_FOLDER_NAME", "save_file_in")
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "storage/outputs"))

# ===== Helpers lấy user trong Flask session =====
def _current_user_from_session_fallback() -> Optional[str]:
    try:
        from flask import session
        u = session.get("user")
        return u if isinstance(u, str) and u else None
    except Exception:
        return None

def _sanitize_user(u: str) -> str:
    return "".join(ch for ch in u if ch.isalnum() or ch in ("-", "_", ".")).strip() or "unknown"

def _token_path_for(user: Optional[str] = None) -> Path:
    """
    Nếu có user → dùng file per-user.
    Nếu không → dùng file chung TOKEN_DB (tương thích ngược).
    """
    if user:
        return TOKEN_DIR / f"drive_token_{_sanitize_user(user)}.json"
    # fallback legacy
    return Path(TOKEN_DB)

# ===== Load/Save token =====
def _load_saved_tokens(user: Optional[str] = None) -> Optional[dict]:
    p = _token_path_for(user)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def _save_tokens(creds: Credentials, user: Optional[str] = None):
    p = _token_path_for(user)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "access_token": getattr(creds, "token", None),
        "refresh_token": getattr(creds, "refresh_token", None),
        "expiry": creds.expiry.isoformat() if getattr(creds, "expiry", None) else None,
        "created_at": int(time.time()),
        "scope": creds.scopes,
        # Lưu kèm client để có thể rebuild Credentials nếu env đổi
        "client_id": getattr(creds, "client_id", None) or GOOGLE_CLIENT_ID,
        "client_secret": getattr(creds, "client_secret", None) or GOOGLE_CLIENT_SECRET,
        "token_uri": getattr(creds, "token_uri", "https://oauth2.googleapis.com/token"),
    }
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _has_drive_connection(username: Optional[str] = None) -> bool:
    saved = _load_saved_tokens(user=username)
    return bool(saved and saved.get("refresh_token"))

# ===== Service builder =====
def _get_drive_service(user: Optional[str] = None):
    """
    Lấy service Google Drive theo đúng token của user.
    - Nếu user=None, lấy từ session['user'] (nếu có), nếu vẫn None → dùng file chung (legacy).
    """
    if user is None:
        user = _current_user_from_session_fallback()

    saved = _load_saved_tokens(user=user)
    if not saved:
        # Thử fallback legacy nếu user có mà file per-user không tồn tại
        if user:
            saved = _load_saved_tokens(user=None)
        if not saved:
            raise RuntimeError("Chưa kết nối Drive cho tài khoản hiện tại.")

    client_id = saved.get("client_id") or GOOGLE_CLIENT_ID
    client_secret = saved.get("client_secret") or GOOGLE_CLIENT_SECRET
    token_uri = saved.get("token_uri") or "https://oauth2.googleapis.com/token"

    if not client_id or not client_secret:
        raise RuntimeError("Thiếu GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.")

    creds = Credentials(
        token=saved.get("access_token"),
        refresh_token=saved.get("refresh_token"),
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            print("[AUTH] Refreshing access token...")
            creds.refresh(Request())
            _save_tokens(creds, user=user)
        else:
            raise RuntimeError("Token không hợp lệ. Hãy 'Ngắt kết nối' rồi 'Kết nối Google Drive' lại.")

    # cache_discovery=False để nhanh & tránh warning
    return build("drive", "v3", credentials=creds, cache_discovery=False)

# ===== Folder utils =====
def _ensure_drive_folder(service, folder_name: str) -> str:
    safe = folder_name.replace("'", "\\'")
    q = f"name = '{safe}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    res = service.files().list(q=q, spaces="drive", fields="files(id,name)", pageSize=1).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]

def _ensure_child_folder(service, parent_id: str, child_name: str) -> str:
    safe = child_name.replace("'", "\\'")
    q = (
        f"name = '{safe}' and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false and '{parent_id}' in parents"
    )
    res = service.files().list(q=q, spaces="drive", fields="files(id,name)", pageSize=1).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {
        "name": child_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]

def _upload_file_to_folder(service, file_path: Path, folder_id: str) -> Dict[str, str]:
    # Đặt đúng mime chuẩn cho JPEG
    mime = "image/jpeg"
    media = MediaFileUpload(str(file_path), mimetype=mime, resumable=False)
    body = {"name": file_path.name, "parents": [folder_id]}
    f = service.files().create(body=body, media_body=media, fields="id,webViewLink").execute()
    return {"id": f.get("id"), "link": f.get("webViewLink")}

def _today_folder_name() -> str:
    """
    Tên thư mục ngày theo yêu cầu: DD_MM_YY.
    Ví dụ: 12/10/2025 -> '12_10_25'
    """
    now = datetime.now()  # local time
    return now.strftime("%d_%m_%y")

def _file_exists_in_folder(service, folder_id: str, filename: str) -> Optional[str]:
    safe = filename.replace("'", "\\'")
    q = f"name = '{safe}' and trashed = false and '{folder_id}' in parents"
    res = service.files().list(q=q, spaces="drive", fields="files(id,name)", pageSize=1).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    return None

# ===== Public API =====
def sync_outputs_to_drive(delete_local: bool = True, user: Optional[str] = None) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """
    Upload tất cả file jpg trong storage/outputs lên Drive/{DRIVE_FOLDER_NAME}/{DD_MM_YY},
    theo token của 'user' tương ứng (hoặc session['user'] nếu user=None).
    Trả về list (filename, file_id, link) — file_id/link là None nếu thất bại hoặc bị bỏ qua.
    """
    if not OUTPUT_DIR.is_dir():
        print(f"[SYNC] OUTPUT_DIR not found: {OUTPUT_DIR}")
        return []

    files = [
        p for p in OUTPUT_DIR.iterdir()
        if p.is_file()
        and p.suffix.lower() == ".jpg"
        and not p.name.endswith((".ok", ".err"))
    ]
    if not files:
        print("[SYNC] No JPG files to upload.")
        return []

    service = _get_drive_service(user=user)

    root_id = _ensure_drive_folder(service, DRIVE_FOLDER_NAME)
    day_folder = _today_folder_name()
    day_id = _ensure_child_folder(service, root_id, day_folder)

    results: List[Tuple[str, Optional[str], Optional[str]]] = []
    for p in files:
        try:
            existing_id = _file_exists_in_folder(service, day_id, p.name)
            if existing_id:
                print(f"[SYNC] Skip {p.name} (already exists on Drive)")
                results.append((p.name, existing_id, None))
                if delete_local:
                    try:
                        p.unlink()
                        print(f"[SYNC] Deleted local (duplicate): {p.name}")
                    except Exception as e:
                        print(f"[SYNC][WARN] Delete failed: {p.name} -> {e}")
                continue

            print(f"[SYNC] Uploading: {p.name} -> {DRIVE_FOLDER_NAME}/{day_folder}")
            up = _upload_file_to_folder(service, p, day_id)
            fid, link = up.get("id"), up.get("link")
            results.append((p.name, fid, link))
            if delete_local and fid:
                try:
                    p.unlink()
                    print(f"[SYNC] Deleted local: {p.name}")
                except Exception as e:
                    print(f"[SYNC][WARN] Delete failed: {p.name} -> {e}")
        except Exception as e:
            print(f"[SYNC][ERROR] Upload failed: {p.name} -> {e}")
            results.append((p.name, None, None))
            # không xoá nếu upload fail

    return results
