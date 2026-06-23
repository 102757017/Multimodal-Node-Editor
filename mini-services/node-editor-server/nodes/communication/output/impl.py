from typing import Dict, Any
from node_editor.node_def import ComputeLogic
from node_editor.dora_state import DoraState
import numpy as np

class DoraOutputLogic(ComputeLogic):
    def compute(self, inputs: Dict[str, Any], properties: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        output_id = properties.get("output_id", "")
        
        # 优先读取连线的图像数据，如果没有图像，再读取连线的文本数据
        data = inputs.get("image")
        if data is None:
            data = inputs.get("text")

        if not output_id:
            return {"__error__": "Please specify an Output ID"}

        if data is not None:
            state = DoraState()
            try:
                data_bytes = b""
                if isinstance(data, str):
                    data_bytes = data.encode('utf-8')
                elif isinstance(data, bytes):
                    data_bytes = data
                elif isinstance(data, np.ndarray):
                    # 把 NumPy 图像数组转成 bytes 发给下游的 Dora 节点
                    data_bytes = data.tobytes()
                else:
                    data_bytes = str(data).encode('utf-8')

                # 发送给 Dora 网络
                state.send_output(output_id, data_bytes)
            except Exception as e:
                return {"__error__": f"Send failed: {e}"}

        return {}
