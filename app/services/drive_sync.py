# app/services/drive_sync.py
import os, json, time, mimetypes
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

# ENV theo test_upload.py
TOKEN_DB = os.environ.get("DRIVE_TOKEN_FILE", "storage/driveSession/drive_tokens.json")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
DRIVE_FOLDER_NAME = os.environ.get("DRIVE_FOLDER_NAME", "save_file_in")

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "storage/outputs"))

def _load_saved_tokens() -> Optional[dict]:
    if not Path(TOKEN_DB).is_file():
        return None
    return json.loads(Path(TOKEN_DB).read_text(encoding="utf-8"))

def _save_tokens(creds: Credentials):
    Path(TOKEN_DB).parent.mkdir(parents=True, exist_ok=True)
    data = {
        "access_token": getattr(creds, "token", None),
        "refresh_token": getattr(creds, "refresh_token", None),
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
        "created_at": int(time.time()),
        "scope": creds.scopes,
    }
    Path(TOKEN_DB).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _get_drive_service():
    saved = _load_saved_tokens()
    if not saved:
        raise RuntimeError("Chưa kết nối Drive. Hãy bấm 'Kết nối Google Drive' trên web trước.")

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("Thiếu GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET trong biến môi trường.")

    creds = Credentials(
        token=saved.get("access_token"),
        refresh_token=saved.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            print("[AUTH] Refreshing access token...")
            creds.refresh(Request())
            _save_tokens(creds)
        else:
            raise RuntimeError("Token không hợp lệ. Hãy 'Ngắt kết nối' rồi 'Kết nối Google Drive' lại.")
    return build("drive", "v3", credentials=creds)

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
    """Tạo/tìm folder con trong parent cụ thể."""
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
    # Chỉ cho phép .jpg — đảm bảo mime chính xác
    mime = "image/jpg"
    media = MediaFileUpload(str(file_path), mimetype=mime, resumable=False)
    body = {"name": file_path.name, "parents": [folder_id]}
    f = service.files().create(body=body, media_body=media, fields="id,webViewLink").execute()
    return {"id": f.get("id"), "link": f.get("webViewLink")}

def _today_folder_name() -> str:
    """
    Tên thư mục ngày theo yêu cầu: DD_MM_YY.
    Ví dụ: 12/10/2025 -> '12_10_25'
    """
    now = datetime.now()  # dùng local time của server/VPS
    return now.strftime("%d_%m_%y")

def _file_exists_in_folder(service, folder_id: str, filename: str) -> Optional[str]:
    """
    Kiểm tra xem trong folder_id đã tồn tại file tên filename chưa.
    Trả về file_id nếu có, None nếu chưa có.
    """
    safe = filename.replace("'", "\\'")
    q = (
        f"name = '{safe}' and trashed = false and '{folder_id}' in parents"
    )
    res = service.files().list(
        q=q, spaces="drive", fields="files(id,name)", pageSize=1
    ).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    return None


def sync_outputs_to_drive(delete_local: bool = True) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """
    Upload tất cả file jpg trong storage/outputs lên Drive/{DRIVE_FOLDER_NAME}/{DD_MM_YY},
    nếu upload OK thì xoá file local.
    Trả về list (filename, file_id, link) — file_id/link là None nếu thất bại hoặc bị bỏ qua.
    """
    if not OUTPUT_DIR.is_dir():
        print(f"[SYNC] OUTPUT_DIR not found: {OUTPUT_DIR}")
        return []

    # Chỉ lấy .jpg và bỏ qua cờ .ok/.err
    files = [
        p for p in OUTPUT_DIR.iterdir()
        if p.is_file()
        and p.suffix.lower() == ".jpg"
        and not p.name.endswith((".ok", ".err"))
    ]
    if not files:
        print("[SYNC] No JPG files to upload.")
        return []

    service = _get_drive_service()

    # Tạo/tham chiếu thư mục gốc (DRIVE_FOLDER_NAME) và thư mục ngày (DD_MM_YY)
    root_id = _ensure_drive_folder(service, DRIVE_FOLDER_NAME)
    day_folder = _today_folder_name()
    day_id = _ensure_child_folder(service, root_id, day_folder)

    results: List[Tuple[str, Optional[str], Optional[str]]] = []
    for p in files:
        try:
            # Kiểm tra tồn tại trước khi upload
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

