# test_upload.py
import os, json, time, mimetypes
from pathlib import Path
from typing import Optional, Dict

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(dotenv_path=Path(__file__).parent / ".env")  # hoặc load_dotenv() nếu .env nằm đúng chỗ


# ===== Cấu hình =====
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# Đường dẫn token do web đã lưu (app/routes.py)
TOKEN_DB = os.environ.get("DRIVE_TOKEN_FILE", "storage/driveSession/drive_tokens.json")

# Client ID/Secret dùng để refresh token
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

# Tên thư mục đích trên Google Drive
DRIVE_FOLDER_NAME = os.environ.get("DRIVE_FOLDER_NAME", "save_file_in")

# File cần upload (chỉnh tại đây)
FILE_PATH = r"D:\SOURCE_CODE\ps_tool\storage\outputs\BCNL-IN-Đen-Adult-M-3805564513-1-3.png"


def _load_saved_tokens() -> Optional[dict]:
    if not os.path.isfile(TOKEN_DB):
        return None
    with open(TOKEN_DB, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_tokens(creds: Credentials):
    os.makedirs(os.path.dirname(TOKEN_DB) or ".", exist_ok=True)
    data = {
        "access_token": getattr(creds, "token", None),
        "refresh_token": getattr(creds, "refresh_token", None),
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
        "created_at": int(time.time()),
        "scope": creds.scopes,
    }
    with open(TOKEN_DB, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_drive_service():
    """
    Tạo Drive service từ token đã lưu bởi web.
    Không mở trình duyệt. Yêu cầu:
      - storage/drive_tokens.json tồn tại & có refresh_token
      - GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET có trong env
    """
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
            # access token không valid và cũng không có refresh_token
            raise RuntimeError("Token không hợp lệ. Hãy 'Ngắt kết nối' rồi 'Kết nối Google Drive' lại.")

    return build("drive", "v3", credentials=creds)


def ensure_drive_folder(service, folder_name: str) -> str:
    """Tìm folder theo tên; nếu chưa có thì tạo. Trả về folder_id."""
    safe_name = folder_name.replace("'", "\\'")
    query = f"name = '{safe_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"

    res = service.files().list(q=query, spaces="drive", fields="files(id,name)", pageSize=10).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]

    meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def upload_file_to_folder(service, file_path: str, folder_id: str) -> Dict[str, Optional[str]]:
    """Upload file vào folder_id."""
    filename = Path(file_path).name
    mime, _ = mimetypes.guess_type(file_path)
    if not mime:
        mime = "application/octet-stream"
    media = MediaFileUpload(file_path, mimetype=mime, resumable=False)
    body = {"name": filename, "parents": [folder_id]}
    print(f"[DRIVE] Uploading '{filename}' ...")
    file = service.files().create(body=body, media_body=media, fields="id,webViewLink").execute()
    return {"id": file.get("id"), "link": file.get("webViewLink")}


def make_public_link(service, file_id: str) -> str:
    """Đặt quyền Anyone-with-link -> reader và lấy link xem."""
    try:
        service.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader"}).execute()
        print("[DRIVE] Sharing set: anyone with the link (viewer)")
    except Exception as e:
        print(f"[DRIVE][WARN] Set sharing failed: {e}")
    info = service.files().get(fileId=file_id, fields="webViewLink").execute()
    return info.get("webViewLink")


def main():
    if not os.path.isfile(FILE_PATH):
        raise FileNotFoundError(f"Input file not found: {FILE_PATH}")
    print(f"[LOCAL] Found file: {FILE_PATH}")

    service = get_drive_service()
    folder_id = ensure_drive_folder(service, DRIVE_FOLDER_NAME)
    result = upload_file_to_folder(service, FILE_PATH, folder_id)

    file_id = result.get("id")
    if not file_id:
        raise RuntimeError("Upload failed: no file_id returned from Drive API")
    public_link = make_public_link(service, file_id)

    print("\n✅ DONE")
    print("   File ID :", file_id)
    print("   View URL:", public_link or result.get("link") or f"https://drive.google.com/file/d/{file_id}/view")


if __name__ == "__main__":
    # Cho phép HTTP trong dev nếu có luồng OAuth khác cần (không bắt buộc cho test này)
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    main()
