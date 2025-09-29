# app/services/drive_sync.py
import os, json, time, mimetypes
from pathlib import Path
from typing import Optional, Dict, List, Tuple

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

def _upload_file_to_folder(service, file_path: Path, folder_id: str) -> Dict[str, str]:
    mime, _ = mimetypes.guess_type(str(file_path))
    if not mime:
        mime = "application/octet-stream"
    media = MediaFileUpload(str(file_path), mimetype=mime, resumable=False)
    body = {"name": file_path.name, "parents": [folder_id]}
    f = service.files().create(body=body, media_body=media, fields="id,webViewLink").execute()
    return {"id": f.get("id"), "link": f.get("webViewLink")}

def sync_outputs_to_drive(delete_local: bool = True) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """
    Upload tất cả file trong storage/outputs lên Drive/{DRIVE_FOLDER_NAME},
    nếu upload OK thì xoá file local.
    Trả về list các tuple (filename, file_id, link) — file_id/link là None nếu thất bại.
    """
    if not OUTPUT_DIR.is_dir():
        print(f"[SYNC] OUTPUT_DIR not found: {OUTPUT_DIR}")
        return []

    files = [p for p in OUTPUT_DIR.iterdir() if p.is_file() and not p.name.endswith((".ok", ".err"))]
    if not files:
        print("[SYNC] No files to upload.")
        return []

    service = _get_drive_service()
    folder_id = _ensure_drive_folder(service, DRIVE_FOLDER_NAME)

    results: List[Tuple[str, Optional[str], Optional[str]]] = []
    for p in files:
        try:
            print(f"[SYNC] Uploading: {p.name}")
            up = _upload_file_to_folder(service, p, folder_id)
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
