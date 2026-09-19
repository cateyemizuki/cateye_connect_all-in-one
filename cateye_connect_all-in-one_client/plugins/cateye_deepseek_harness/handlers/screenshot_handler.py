"""截图命令处理（高斯模糊在本地侧执行）。"""

import base64
from io import BytesIO
from typing import Optional

try:
    from PIL import Image, ImageGrab, ImageFilter
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def take_screenshot(blur_radius: int = 0) -> Optional[str]:
    if not HAS_PIL:
        return None
    try:
        img = ImageGrab.grab(all_screens=True)
        if blur_radius > 0:
            img = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


class ScreenshotHandler:
    def __init__(self, config: dict, ctx):
        del ctx
        self.enabled = config.get("features", {}).get("screenshot", True)
        sc = config.get("screenshot", {})
        self.default_blur = sc.get("blur_radius", 5)

    def apply_config(self, config: dict) -> None:
        """WebUI 修改配置后就地应用（不重启框架）。"""
        self.enabled = config.get("features", {}).get("screenshot", True)
        self.default_blur = config.get("screenshot", {}).get("blur_radius", self.default_blur)

    def handle(self, call_id: str, args: dict) -> dict:
        if not self.enabled:
            return {"call_id": call_id, "result": "", "error": "功能未启用: screenshot"}

        blur = int(args.get("blur_radius", self.default_blur))
        # 钳制范围：异常大半径会导致 PIL 模糊计算 CPU/内存失控
        blur = max(0, min(blur, 64))
        img_b64 = take_screenshot(blur)
        if img_b64:
            return {"call_id": call_id, "result": img_b64, "error": ""}
        return {"call_id": call_id, "result": "", "error": "截图失败：无法捕获屏幕"}
