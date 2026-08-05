"""模板系统 — 变量替换 + 批量生成。"""

import json
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from clipwright.config import logger

TEMPLATES_DIR = Path("templates")
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

_INTRO_OUTRO_DIR = Path("intro_outro")
_INTRO_OUTRO_DIR.mkdir(parents=True, exist_ok=True)


class IntroOutroConfig(BaseModel):
    """片头/片尾配置。"""
    name: str = ""
    kind: str = "intro"
    duration_sec: float = 3.0
    text: str = ""
    animation: str = "fade_in"
    bg_color: str = "#1a1a2e"
    font_color: str = "#ffffff"
    font_size: int = 72
    audio_path: str = ""

    def to_dict(self) -> dict:
        return self.model_dump()

    @classmethod
    def from_dict(cls, d: dict) -> IntroOutroConfig:
        return cls(**d)

    @classmethod
    def list_all(cls) -> list[IntroOutroConfig]:
        if not _INTRO_OUTRO_DIR.exists():
            return []
        result = []
        for f in sorted(_INTRO_OUTRO_DIR.iterdir()):
            if f.suffix == ".json":
                import json
                try:
                    result.append(cls.from_dict(json.loads(f.read_text(encoding="utf-8"))))
                except Exception:
                    pass
        return result

    def save(self) -> None:
        import json
        path = _INTRO_OUTRO_DIR / f"{self.name}.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def delete(cls, name: str) -> bool:
        path = _INTRO_OUTRO_DIR / f"{name}.json"
        if path.exists():
            path.unlink()
            return True
        return False


class VideoTemplate(BaseModel):
    """视频模板 — 包含变量占位符的完整管线配置。"""

    template_id: str = ""
    name: str = ""
    description: str = ""
    persona_id: str = ""
    category_plugin_id: str = ""
    topic_template: str = ""
    script_template: str = ""
    extra_params: dict[str, Any] = {}
    orientation: str = "landscape"
    resolution: str = "1920x1080"
    fps: int = 30
    bitrate: str = "5M"
    intro_config: Optional[dict] = None
    outro_config: Optional[dict] = None
    brand_colors: list[str] = []

    def render(self, variables: dict[str, str]) -> dict[str, Any]:
        """将变量填入模板，生成完整的管线请求。"""
        def _fill(text: str) -> str:
            for k, v in variables.items():
                text = text.replace("{{" + k + "}}", v)
            return text

        topic = _fill(self.topic_template)
        script = _fill(self.script_template)
        extra = {}
        for k, v in self.extra_params.items():
            if isinstance(v, str):
                extra[k] = _fill(v)
            else:
                extra[k] = v

        return {
            "persona_id": self.persona_id,
            "category_plugin_id": self.category_plugin_id,
            "topic": topic,
            "script": script,
            "extra_params": {
                **extra,
                "orientation": self.orientation,
                "script_text": script,
            },
            "render_settings": {
                "width": int(self.resolution.split("x")[0]),
                "height": int(self.resolution.split("x")[1]),
                "fps": self.fps,
                "bitrate": self.bitrate,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, d: dict) -> VideoTemplate:
        return cls(**d)

    def save(self) -> None:
        path = TEMPLATES_DIR / f"{self.template_id}.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, template_id: str) -> VideoTemplate | None:
        path = TEMPLATES_DIR / f"{template_id}.json"
        if not path.exists():
            return None
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def list_all(cls) -> list[VideoTemplate]:
        if not TEMPLATES_DIR.exists():
            return []
        result = []
        for f in sorted(TEMPLATES_DIR.iterdir()):
            if f.suffix == ".json":
                try:
                    result.append(cls.from_dict(json.loads(f.read_text(encoding="utf-8"))))
                except Exception as e:
                    logger.warning("加载模板失败 %s: %s", f.name, e)
        return result

    @classmethod
    def delete(cls, template_id: str) -> bool:
        path = TEMPLATES_DIR / f"{template_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False


def extract_variables(text: str) -> list[str]:
    """从文本中提取所有 {{变量名}}。"""
    return re.findall(r"\{\{(\w+)\}\}", text)


def batch_generate(template_id: str, variables_list: list[dict[str, str]]) -> list[dict[str, Any]]:
    """对一组变量批量渲染模板。"""
    template = VideoTemplate.load(template_id)
    if not template:
        raise ValueError(f"Template not found: {template_id}")
    return [template.render(vars) for vars in variables_list]
