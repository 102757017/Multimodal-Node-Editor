from typing import Dict, Any
from node_editor.node_def import ComputeLogic
import json


class CropNodeLogic(ComputeLogic):
    """
    图像裁剪节点逻辑
    使用归一化坐标（0.0～1.0）指定裁剪区域
    输出：裁剪后的图像 + 裁剪区域坐标（JSON格式）
    """

    def compute(
        self,
        inputs: Dict[str, Any],
        properties: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        # 获取输入图像
        img = inputs.get("image")
        if img is None:
            return {"cropped_image": None, "crop_bbox": None}

        # 获取归一化坐标（0.0～1.0）
        min_x = float(properties.get("min_x", 0.0))
        min_y = float(properties.get("min_y", 0.0))
        max_x = float(properties.get("max_x", 1.0))
        max_y = float(properties.get("max_y", 1.0))

        # 将坐标值限制在0.0～1.0范围内
        min_x = max(0.0, min(1.0, min_x))
        min_y = max(0.0, min(1.0, min_y))
        max_x = max(0.0, min(1.0, max_x))
        max_y = max(0.0, min(1.0, max_y))

        # 如果最小值大于最大值，则交换它们
        if min_x > max_x:
            min_x, max_x = max_x, min_x
        if min_y > max_y:
            min_y, max_y = max_y, min_y

        # 获取图像尺寸
        height, width = img.shape[:2]

        # 将归一化坐标转换为像素坐标
        x1 = int(min_x * width)
        y1 = int(min_y * height)
        x2 = int(max_x * width)
        y2 = int(max_y * height)

        # 构建裁剪坐标JSON（只包含x1,y1,x2,y2）
        bbox_coords = [x1, y1, x2, y2]
        bbox_json = json.dumps(bbox_coords)

        # 如果裁剪区域无效（面积为0），返回原图
        if x2 <= x1 or y2 <= y1:
            return {
                "cropped_image": img,
                "crop_bbox": bbox_json
            }

        # 执行裁剪操作
        result = img[y1:y2, x1:x2].copy()

        return {
            "cropped_image": result,
            "crop_bbox": bbox_json
        }
