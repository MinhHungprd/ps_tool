from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import os
import io
import requests
from app.config import CREDENTIALS_FILE, SCOPES

def get_drive_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)

def download_from_share_link(link):
    # Ví dụ: https://drive.google.com/file/d/FILE_ID/view?usp=sharing -> Tải bằng requests
    file_id = link.split('/d/')[1].split('/')[0]
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.content
    else:
        raise Exception("Lỗi tải file")

def upload_to_drive(file_path, filename):
    service = get_drive_service()
    file_metadata = {'name': filename}
    media = MediaFileUpload(file_path, mimetype='image/jpeg')
    file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    return f"https://drive.google.com/file/d/{file.get('id')}/view"