"""node_editor.image_utils shim — provides ensure_base64 for upstream nodes."""
import base64
import io


def ensure_base64(value, max_size: int = 960, quality: int = 95):
    """Best-effort conversion of image-like values to a base64 data URI.

    Accepts numpy arrays, PIL images, or base64 strings.  Falls back to
    returning the value unchanged if conversion fails (e.g. PIL missing).
    """
    if value is None:
        return None
    # already a base64 data URI string
    if isinstance(value, str) and value.startswith("data:"):
        return value
    if isinstance(value, str):
        # assume raw base64
        return value
    # numpy array
    try:
        import numpy as np
        if isinstance(value, np.ndarray):
            try:
                from PIL import Image
                img = Image.fromarray(value)
                img.thumbnail((max_size, max_size))
                buf = io.BytesIO()
                fmt = "JPEG" if img.mode in ("RGB", "L") else "PNG"
                img.save(buf, format=fmt, quality=quality)
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                return f"data:image/{fmt.lower()};base64,{b64}"
            except Exception:
                return None
    except ImportError:
        pass
    # PIL Image
    try:
        from PIL import Image
        if isinstance(value, Image.Image):
            img = value.copy()
            img.thumbnail((max_size, max_size))
            buf = io.BytesIO()
            fmt = "JPEG" if img.mode in ("RGB", "L") else "PNG"
            img.save(buf, format=fmt, quality=quality)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/{fmt.lower()};base64,{b64}"
    except Exception:
        pass
    return value
