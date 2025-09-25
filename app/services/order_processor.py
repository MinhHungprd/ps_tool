import pandas as pd
from app.services.google_drive import download_from_share_link
from app.services.psd_handler import update_psd
from app.config import TEMPLATES_DIR, OUTPUTS_DIR
import os

def process_order_from_excel(excel_path):
    df = pd.read_excel(excel_path)
    results = []
    for _, row in df.iterrows():
        order_id = row['Order ID']
        design_link = row['File thiết kế']
        type_size = row['Loại áo']  # Ví dụ: BCNL-IN S
        
        # Download JPG design
        design_data = download_from_share_link(design_link)
        
        # Determine template path
        type_folder = type_size.split()[0]  # e.g., BCNL-IN
        size = type_size.split()[1]  # e.g., S
        template_path = os.path.join(TEMPLATES_DIR, type_folder, size, 'sơ_đồ_áo.psd')
        
        # Output file name
        output_name = f"{type_folder}-Đen-Adult {size}-{order_id}.jpg"
        output_path = os.path.join(OUTPUTS_DIR, output_name)
        
        # Update PSD and export
        success = update_psd(template_path, design_data, output_name, output_path)
        if success:
            # Upload to Google Drive
            drive_link = upload_to_drive(output_path, output_name)
            results.append({'order_id': order_id, 'output_link': drive_link})
        else:
            results.append({'order_id': order_id, 'output_link': 'Error processing'})
    
    return results