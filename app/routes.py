from flask import Blueprint, request, render_template, jsonify
from app.services.order_processor import process_order_from_excel
from app.services.psd_handler import relink_and_export_batch
import os

main = Blueprint('main', __name__)

@main.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        file = request.files['excel_file']
        limit = int(request.form.get('limit', 2))  # có thể thêm input limit trong form
        if file:
            excel_path = os.path.join('storage', file.filename)
            file.save(excel_path)

            job = process_order_from_excel(excel_path,sheet ='data', limit=limit)  # chuẩn bị jobs
            relink_and_export_batch(job)                                   # render batch (in-place)

            return jsonify(job)
    return render_template('index.html')
