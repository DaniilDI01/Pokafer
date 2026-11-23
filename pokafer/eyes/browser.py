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


class TableBrowser:
    """Браузер с точным viewport: открыл сайт, даёт кадры и вводные события."""

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

    # ---------- действия (актатор) ----------

    def click_at(self, x: int, y: int) -> None:
        """Клик по координатам viewport'а (реальное событие мыши)."""
        from selenium.webdriver.common.action_chains import ActionChains

        ActionChains(self._driver).move_to_element_with_offset(
            self._driver.find_element("tag name", "body"),
            0, 0,
        ).perform()
        # body в (0,0) viewport: смещение от левого верхнего угла
        ActionChains(self._driver).move_to_element_with_offset(
            self._driver.find_element("tag name", "body"),
            x, y,
        ).click().perform()

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
