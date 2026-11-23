"""Реестр сайтов для глаз: домены -> пресет калибровки.

data/eyes/sites.toml:
    [presets.pokafer_local]
    url_patterns = ["localhost:8010", "127.0.0.1:8010"]
    calibration = "pokafer_local.toml"

    [domains]
    "127.0.0.1" = "pokafer_local"

Калибровочный файл пресета: зоны в ПРОЦЕНТАХ от окна сайта (переживают
resize/DPI), шаблоны карт/цифр — имена файлов-наборов.

Зоны задаются как [x, y, w, h] в долях окна. Каждый именованный узел
(например [seat.pos_top]) содержит произвольный набор под-зон; код движка
читает их по именам, о которых договорились (cards/stack/bet/...).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

EYES_DIR = Path(__file__).resolve().parents[2] / "data" / "eyes"


def _norm_rects(node: dict) -> dict:
    """Рекурсивно: все 4-списки помечаем как зоны (валидация формы)."""
    out = {}
    for key, val in node.items():
        if isinstance(val, dict):
            out[key] = _norm_rects(val)
        elif isinstance(val, list) and len(val) == 4 and all(
            isinstance(v, (int, float)) for v in val
        ):
            x, y, w, h = val
            if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
                raise ValueError(f"зона {key} вне диапазона [0..1]: {val}")
            out[key] = tuple(val)
        else:
            out[key] = val
    return out


@dataclass(frozen=True)
class Preset:
    """Калибровка одного сайта: зоны в долях окна + тема/шаблоны."""

    name: str
    url_patterns: tuple[str, ...]
    theme: str
    zones: dict          # вложенные зоны в долях окна
    card_templates: str  # имя набора шаблонов карт в data/eyes/templates/

    def zone(self, path: str, window: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """Зона по пути 'seat.pos_top.cards' -> пиксели окна (x, y, w, h)."""
        node: dict = self.zones
        for part in path.split("."):
            node = node[part]  # type: ignore[index]
        if not (isinstance(node, tuple) and len(node) == 4):
            raise KeyError(f"нет зоны {path!r} в пресете {self.name!r}")
        wx, wy, ww, wh = window
        fx, fy, fw, fh = node
        return (round(wx + fx * ww), round(wy + fy * wh),
                round(fw * ww), round(fh * wh))


def load_registry(path: Path | None = None) -> dict:
    """ sites.toml: {'presets': {...}, 'domains': {...}}."""
    path = path or (EYES_DIR / "sites.toml")
    with open(path, "rb") as f:
        return tomllib.load(f)


def preset_for_domain(domain: str, registry: dict | None = None,
                      path: Path | None = None) -> Preset:
    """Пресет по домену/порту вкладки; KeyError с понятным текстом."""
    reg = registry if registry is not None else load_registry(path)
    domain = domain.lower().strip()
    if ":" in domain:  # host:port -> host
        domain = domain.rsplit(":", 1)[0]
    preset_name = None
    for dom, name in reg.get("domains", {}).items():
        if domain == dom.lower() or domain.endswith("." + dom.lower()):
            preset_name = name
            break
    if preset_name is None:
        known = ", ".join(sorted(reg.get("domains", {})))
        raise KeyError(f"для домена {domain!r} нет пресета; известны: {known}")
    return load_preset(preset_name, reg, path)


def load_preset(name: str, registry: dict | None = None,
                path: Path | None = None) -> Preset:
    reg = registry if registry is not None else load_registry(path)
    try:
        spec = reg["presets"][name]
    except KeyError as e:
        raise KeyError(f"пресет {name!r} не описан в sites.toml") from e
    cal_path = EYES_DIR / spec["calibration"]
    with open(cal_path, "rb") as f:
        zones = _norm_rects(tomllib.load(f))
    return Preset(
        name=name,
        url_patterns=tuple(spec.get("url_patterns", ())),
        theme=spec.get("theme", "default"),
        zones=zones,
        card_templates=spec.get("card_templates", name),
    )
