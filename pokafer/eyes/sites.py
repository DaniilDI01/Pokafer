"""Реестр сайтов для глаз: домены -> пресет калибровки.

data/eyes/sites.toml:
    [presets.pokafer_local]
    url_patterns = ["localhost:8010", "127.0.0.1:8010"]
    calibration = "pokafer_local.toml"

    [domains]
    "127.0.0.1" = "pokafer_local"

Файл калибровки (пресета) описывает, ЧТО где лежит на экране сайта:

    [meta]
    viewport_ref = [1280, 720]   # вьюпорт, на котором снималась калибровка
    coords = "ref_px"            # зоны — пиксели референс-вьюпорта
    calibration_id = "..."       # меняется при любой зоне; шаблоны привязаны к нему

    [hero]
    cards  = [[x, y, w, h], [x, y, w, h]]   # слоты карт (список зон)
    [board]
    slots  = [[x, y, w, h] × 5]
    [seat.pos-tl]
    cards  = [[...], [...]]     # слоты вскрытых карт места
    backs  = [[...], [...]]     # слоты рубашек
    stack  = [x, y, w, h]       # одиночная зона

Зоны хранятся долями окна (переживают resize/DPI), а преобразуются в пиксели
по режиму coords:
  "fraction" — доля ТЕКУЩЕГО окна (масштабируемые сайты);
  "ref_px"   — пиксели референс-вьюпорта (сайт не масштабируется: стол
               фиксированного размера в любом окне).
Слоты — просто списки зон; код движка читает узлы по договорённым именам
(cards/backs/slots/stack/...), не зная сайта.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

EYES_DIR = Path(__file__).resolve().parents[2] / "data" / "eyes"

Rect = tuple[int, int, int, int]


def _is_rect(val: object) -> bool:
    return (isinstance(val, (list, tuple)) and len(val) == 4
            and all(isinstance(v, (int, float)) for v in val))


def _checked(key: str, val: list) -> tuple:
    x, y, w, h = val
    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
        raise ValueError(f"зона {key} вне диапазона [0..1]: {val}")
    return tuple(val)


def _norm_node(node: dict, path: str = "") -> dict:
    """Рекурсивно: 4-список — зона, список зон — слоты (валидация формы)."""
    out: dict = {}
    for key, val in node.items():
        name = f"{path}.{key}" if path else key
        if isinstance(val, dict):
            out[key] = _norm_node(val, name)
        elif _is_rect(val):
            out[key] = _checked(name, val)
        elif isinstance(val, list) and val and all(_is_rect(v) for v in val):
            out[key] = [_checked(name, v) for v in val]
        else:
            out[key] = val
    return out


@dataclass(frozen=True)
class Preset:
    """Калибровка одного сайта: зоны в долях окна + тема/шаблоны."""

    name: str
    url_patterns: tuple[str, ...]
    theme: str
    zones: dict                   # вложенные узлы: зоны, списки зон, прочее
    card_templates: str           # имя набора шаблонов в data/eyes/templates/
    viewport_ref: tuple[int, int] | None
    coords: str                   # "fraction" | "ref_px"
    calibration_id: str | None    # версии зон (шаблоны привязаны к нему)

    def node(self, path: str):
        """Узел пресета по пути 'hero.cards' / 'board.slots' / 'seat.pos-tl'."""
        node = self.zones
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise KeyError(f"нет узла {path!r} в пресете {self.name!r}")
            node = node[part]
        return node

    def _to_px(self, rect, window: tuple[int, int, int, int]) -> Rect:
        wx, wy, ww, wh = window
        fx, fy, fw, fh = rect
        if self.coords == "ref_px" and self.viewport_ref:
            ww, wh = self.viewport_ref  # px референс-вьюпорта от угла окна
        return (wx + round(fx * ww), wy + round(fy * wh),
                round(fw * ww), round(fh * wh))

    def zone(self, path: str, window: tuple[int, int, int, int]) -> Rect:
        """Зона по пути 'pot.area' -> пиксели (x, y, w, h) в окне."""
        node = self.node(path)
        if not _is_rect(node):
            raise KeyError(f"узел {path!r} в пресете {self.name!r} — не зона")
        return self._to_px(node, window)

    def slots(self, path: str, window: tuple[int, int, int, int]) -> list[Rect]:
        """Список зон-слотов по пути ('hero.cards', 'board.slots') -> пиксели."""
        node = self.node(path)
        if not (isinstance(node, list) and node and all(_is_rect(n) for n in node)):
            raise KeyError(f"узел {path!r} в пресете {self.name!r} — не список слотов")
        return [self._to_px(n, window) for n in node]


def load_registry(path: Path | None = None) -> dict:
    """sites.toml: {'presets': {...}, 'domains': {...}}."""
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
        zones = _norm_node(tomllib.load(f))
    meta = zones.get("meta", {})
    viewport_ref = meta.get("viewport_ref")
    coords = meta.get("coords", "fraction")
    if coords not in ("fraction", "ref_px"):
        raise ValueError(f"coords={coords!r}: ожидалось 'fraction' или 'ref_px'")
    if coords == "ref_px" and not (viewport_ref and len(viewport_ref) == 2):
        raise ValueError("coords='ref_px' требует meta.viewport_ref = [w, h]")
    return Preset(
        name=name,
        url_patterns=tuple(spec.get("url_patterns", ())),
        theme=spec.get("theme", "default"),
        zones=zones,
        card_templates=spec.get("card_templates", name),
        viewport_ref=tuple(viewport_ref) if viewport_ref else None,
        coords=coords,
        calibration_id=meta.get("calibration_id"),
    )
