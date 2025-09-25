from psd_tools import PSDImage
from psd_tools.api.layers import SmartObjectLayer
from PIL import Image
import os

def update_psd(template_path, design_jpg_data, order_name, output_path):
    psd = PSDImage.open(template_path)
    
    # Cập nhật smart objects (giả sử layer tên 'design_layer' là smart object)
    for layer in psd:
        if isinstance(layer, SmartObjectLayer) and layer.name == 'design_layer':
            # Thay thế content smart object
            smart_obj = layer.smart_object
            smart_obj.replace_contents(Image.open(io.BytesIO(design_jpg_data)))  # Thay bằng JPG mới
    
    # Cập nhật layer text (giả sử layer tên 'text_layer')
    for layer in psd:
        if layer.kind == 'type' and layer.name == 'text_layer':
            layer.text = order_name
    
    # Xuất JPG
    psd.composite().save(output_path)