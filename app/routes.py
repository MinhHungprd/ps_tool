from flask import Blueprint, request, render_template, jsonify
from app.services.order_processor import process_order_from_excel
import os

main = Blueprint('main', __name__)

@main.route('/', methods=['GET', 'POST'])

def index():
    if request.method == 'POST':
        file = request.files['excel_file']
        if file:
            excel_path = os.path.join('storage', file.filename)
            file.save(excel_path)
            results = process_order_from_excel(excel_path)
            return jsonify(results)
    return render_template('index.html')