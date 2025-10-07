from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import os
import io
import requests
import re
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
    # Trích xuất file_id từ link chia sẻ
    file_id = link.split('/d/')[1].split('/')[0]
    base_url = "https://drive.google.com/uc"
    params = {'export': 'download', 'id': file_id}
    
    session = requests.Session()
    response = session.get(base_url, params=params, stream=True)
    
    # Kiểm tra nếu là tải trực tiếp (có Content-Disposition)
    if 'Content-Disposition' in response.headers:
        return response.content
    
    # Nếu không, trích xuất action URL và confirm token từ HTML
    text = response.text
    
    # Tìm action URL từ form
    action_match = re.search(r'<form id="download-form" action="([^"]+)"', text)
    if action_match:
        base_url = action_match.group(1)
    
    # Tìm confirm token từ input
    confirm_match = re.search(r'<input[^>]*name="confirm"[^>]*value="([^"]+)"', text)
    token = confirm_match.group(1) if confirm_match else None
    
    # Kiểm tra cookie cho download_warning
    if not token:
        for key, value in response.cookies.items():
            if key.startswith('download_warning'):
                token = value
                break
    
    if token:
        params['confirm'] = token
        response = session.get(base_url, params=params, stream=True)
        
        if 'Content-Disposition' in response.headers:
            return response.content
        else:
            raise Exception("Lỗi tải file: Không thể tải sau khi xác nhận")
    else:
        raise Exception("Lỗi tải file: Không tìm thấy token xác nhận")