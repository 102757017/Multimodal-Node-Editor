from typing import Dict, Any
from node_editor.node_def import ComputeLogic
from node_editor.dora_state import DoraState
import numpy as np
import json

class DoraInputLogic(ComputeLogic):
    def compute(self, inputs: Dict[str, Any], properties: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        input_id = properties.get("input_id", "")
        if not input_id:
            return {"image": None, "text": None}

        state = DoraState()
        entry = state.get_input(input_id)
        
        if entry is None:
            return {"image": None, "text": None}

        raw_value = entry["value"]
        metadata = entry["metadata"] or {} # 防止 metadata 为 None

        img = None
        info_list = []

        # 1. 尝试利用元数据重建 NumPy 图像数组
        try:
            if isinstance(raw_value, bytes) and "shape" in metadata and "dtype" in metadata:
                # 从字节流还原，指定 dtype (例如 'uint8') 并 reshape (例如 [720, 1280, 3])
                shape = metadata["shape"]
                dtype = metadata["dtype"]
                img = np.frombuffer(raw_value, dtype=dtype).reshape(shape)
            elif isinstance(raw_value, np.ndarray):
                img = raw_value
        except Exception as e:
            return {"image": None, "text": f"Image Rebuild Error: {e}"}

        # 2. 将其他元数据转为文本显示
        # 包含 camera_name, step_id, product_id 等
        important_keys = ["camera_name", "step_id", "product_id"]
        for key in important_keys:
            if key in metadata:
                info_list.append(f"{key}: {metadata[key]}")
        
        # 如果不是图像，且 metadata 为空，尝试直接转 string
        if img is None and not info_list:
            if isinstance(raw_value, bytes):
                info_list.append(raw_value.decode('utf-8', errors='ignore'))
            else:
                info_list.append(str(raw_value))

        return {
            "image": img, 
            "text": "\n".join(info_list) if info_list else None
        }
