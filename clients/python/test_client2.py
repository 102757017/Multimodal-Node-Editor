# 终端 2：脚本推送图片，同时在浏览器观察图执行
from multimodal_client import HttpClient
import cv2

client = HttpClient("http://localhost:3030")
print(client.ping())  # {'ok': True, 'mode': 'gui-http'}

info = client.graph_info()
img_node = info.find_node_by_name("Image")

result = client.run(
    image_node_id=img_node.id,
    image_array=cv2.imread("1.jpg"),
    output_node_id=img_node.id,
    output_port_name="image_out",
)
result.save_output("output.jpg")
