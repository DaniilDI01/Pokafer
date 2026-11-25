"""Управляемый браузер для глаз: фиксированный viewport, headless, профиль.

Зачем selenium, а не «запустить браузер руками»: пропорции страницы должны
быть детерминированными (зоны глаз в % от viewport), работать на любом
мониторе или вовсе без него (headless), куки/сессия — в персистентном
профиле. Браузер указывается именем (chrome/chromium/firefox).

Скриншот driver.get_screenshot_as_png() — это пиксели viewport'а: ровно то,
что видят распознаватели. Действия — реальные вводные события
(ActionChains: клики/клавиатура) по координатам, которые выдал трекер.
"""
from __future__ import annotations

import time
from pathlib import Path

from PIL import Image

_PATCH = 32  # метка совмещения кадра с экраном, px


class TableBrowser:
    """Браузер с точным viewport: открыл сайт, даёт кадры и вводные события."""

    # Текст — с серым сглаживанием и в видимом окне (там Chrome иначе рисует
    # глифы с цветным LCD-сглаживанием). Это НЕ делает рендер текста
    # одинаковым в headless и на экране — он зависит от видеокарты и шрифтов
    # машины; потому лица карт стола — спрайты, а шаблоны текста (цифры)
    # снимаются в том режиме, в котором глаза работают.
    RENDER_FLAGS: tuple[str, ...] = ("--disable-lcd-text",)

    def __init__(self, url: str, viewport: tuple[int, int] = (1280, 720),
                 browser: str = "chrome", headless: bool = False,
                 profile_dir: str | None = None, page_load_timeout: int = 15):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options as ChromeOptions

        vw, vh = viewport
        opts = ChromeOptions()
        if browser in ("chromium",):
            opts.binary_location = "/usr/bin/chromium"
        opts.add_argument("--force-device-scale-factor=1")
        opts.add_argument("--disable-infobars")
        opts.add_argument("--disable-extensions")
        opts.add_argument(f"--window-size={vw + 40},{vh + 140}")
        opts.add_argument("--window-position=0,0")
        for flag in self.RENDER_FLAGS:
            opts.add_argument(flag)
        if headless:
            opts.add_argument("--headless=new")
        if profile_dir:
            opts.add_argument(f"--user-data-dir={Path(profile_dir).resolve()}")

        self._driver = webdriver.Chrome(options=opts)
        self._driver.set_page_load_timeout(page_load_timeout)
        self.viewport = (vw, vh)
        self.url = url
        self._driver.get(url)
        self._fit_window(vw, vh)  # в headless тоже: окно = viewport
        self._wait_page(vw, vh)

    # ---------- служебное ----------

    def _fit_window(self, vw: int, vh: int) -> None:
        """Подгон окна: chrome UI съедает высоту — компенсируем разницей."""
        for _ in range(4):
            inner = self._driver.execute_script(
                "return [window.innerWidth, window.innerHeight];"
            )
            dw, dh = vw - inner[0], vh - inner[1]
            if dw == 0 and dh == 0:
                return
            size = self._driver.get_window_size()
            self._driver.set_window_size(size["width"] + dw, size["height"] + dh)
            time.sleep(0.15)

    def _wait_page(self, vw: int, vh: int) -> None:
        deadline = time.time() + 10
        while time.time() < deadline:
            inner = self._driver.execute_script(
                "return [window.innerWidth, window.innerHeight];"
            )
            if (inner[0], inner[1]) == (vw, vh):
                return
            time.sleep(0.2)
        raise RuntimeError(
            f"viewport не установлен: {inner} вместо {(vw, vh)}"
        )

    # ---------- восприятие ----------

    def screenshot(self) -> Image.Image:
        """Пиксели viewport'а (то, что видят распознаватели)."""
        png = self._driver.get_screenshot_as_png()
        import io

        img = Image.open(io.BytesIO(png)).convert("RGB")
        vw, vh = self.viewport
        if img.size != (vw, vh):
            img = img.crop((0, 0, vw, vh))
        return img

    def execute(self, script: str, *args):
        """JS в странице — ТОЛЬКО для диагностики/калибровки, не для глаз."""
        return self._driver.execute_script(script, *args)

    def locate_on_screen(self) -> tuple[int, int]:
        """Где на экране лежит угол viewport'а окна: (x, y) — область для mss.

        Окно ищется по факту, а не по window.screenX (рамка оконного
        менеджера и панель браузера дают заранее неизвестное смещение): кадр
        CDP совмещается с экраном по точному совпадению контрастной метки.
        Только для наладки захвата: сам боевой захват — пиксели экрана.
        """
        from pokafer.eyes.capture import capture_full

        for _ in range(4):  # экран может отставать от страницы на кадр
            probe = self.screenshot()
            screen, geo = capture_full()
            found = find_on_screen(probe, screen, geo)
            if found is not None:
                return found
            time.sleep(0.15)
        raise RuntimeError("окно браузера не найдено на экране: перекрыто, свёрнуто "
                           "или его содержимое ещё меняется")

    # ---------- действия (актатор) ----------

    def click_at(self, x: int, y: int) -> None:
        """Клик по координатам viewport'а (реальное событие мыши).

        origin='viewport' — координаты от левого верхнего угла страницы.
        ActionChains.move_to_element_with_offset в Selenium 4 отсчитывает от
        ЦЕНТРА элемента: клик «по углу body» попадал на пол-экрана мимо.
        """
        from selenium.webdriver.common.actions.action_builder import ActionBuilder
        from selenium.webdriver.common.actions.mouse_button import MouseButton

        builder = ActionBuilder(self._driver)
        mouse = builder.add_pointer_input("mouse", "mouse")
        mouse.create_pointer_move(origin="viewport", x=int(x), y=int(y))
        mouse.create_pointer_down(button=MouseButton.LEFT)
        mouse.create_pointer_up(button=MouseButton.LEFT)
        builder.perform()

    def type_text(self, text: str) -> None:
        """Клавиатура: посимвольный ввод в активный элемент."""
        from selenium.webdriver.common.action_chains import ActionChains

        ActionChains(self._driver).send_keys(text).perform()

    def key(self, key_name: str) -> None:
        """Одиночная клавиша (ENTER, TAB, ...)."""
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys

        ActionChains(self._driver).send_keys(getattr(Keys, key_name, key_name)).perform()

    def close(self) -> None:
        try:
            self._driver.quit()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def find_on_screen(probe: Image.Image, screen: Image.Image,
                   geo: dict) -> tuple[int, int] | None:
    """Позиция (x, y) кадра `probe` внутри снимка экрана: точное совпадение.

    Ищется по строке самой контрастной метки кадра (bytes.find — быстро),
    затем сверяется вся метка и центральная полоса кадра (верх/низ окна
    может перекрывать рамка). None — окно перекрыто или кадр ещё не на экране.
    """
    pw, ph = probe.size
    left, top = geo["left"], geo["top"]
    sw, sh = screen.size
    if pw > sw or ph > sh:
        return None
    pdata, sdata = probe.tobytes(), screen.tobytes()
    ps, ss = pw * 3, sw * 3

    def block_colors(bx: int, by: int) -> int:
        return len({pdata[(by + y) * ps + (bx + x) * 3:(by + y) * ps + (bx + x) * 3 + 3]
                    for y in range(0, _PATCH, 4) for x in range(0, _PATCH, 4)})

    # метка — не однотонный блок в средней части кадра (там не бывает рамки окна)
    bx, by = max(((x, y) for y in range(ph // 4, 3 * ph // 4 - _PATCH, 16)
                  for x in range(pw // 4, 3 * pw // 4 - _PATCH, 16)),
                 key=lambda b: block_colors(*b))
    patch_rows = [pdata[(by + dy) * ps + bx * 3:(by + dy) * ps + (bx + _PATCH) * 3]
                  for dy in range(_PATCH)]
    needle = patch_rows[0]
    hits: list[tuple[int, int]] = []
    for sy in range(top, top + sh):  # строка МЕТКИ на экране — любая
        row = sdata[sy * ss:sy * ss + ss]
        start = row.find(needle)
        while start != -1:
            if start % 3 == 0:
                sx = start // 3
                if all(sdata[(sy + dy) * ss + sx * 3:(sy + dy) * ss + sx * 3 + _PATCH * 3]
                       == patch_rows[dy] for dy in range(1, _PATCH)):
                    # угол окна = позиция метки минус её место в кадре
                    hits.append((sx - bx, sy - by))
            start = row.find(needle, start + 1)
    # окно целиком на экране (в пределах virtual screen)
    hits = [(x, y) for x, y in hits
            if left <= x and top <= y and x + pw <= left + sw and y + ph <= top + sh]
    if len(hits) != 1:
        return None
    x, y = hits[0]
    y0, y1 = 16, ph - 16  # центральная полоса: рамка окна сверху/снизу не мешает
    for py in range(y0, y1):
        a = pdata[py * ps:py * ps + ps]
        o = (y + py) * ss + x * 3
        if sdata[o:o + ps] != a:
            return None
    return x, y
