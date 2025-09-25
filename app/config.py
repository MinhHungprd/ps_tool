import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, '../storage/templates')
DESIGNS_DIR = os.path.join(BASE_DIR, '../storage/designs')
OUTPUTS_DIR = os.path.join(BASE_DIR, '../storage/outputs')

# Google Drive API
CREDENTIALS_FILE = os.path.join(BASE_DIR, '../credentials.json')
SCOPES = ['https://www.googleapis.com/auth/drive']