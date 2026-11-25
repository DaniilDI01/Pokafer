"""Глаза без браузера: пресет со слотами, читатель карт, ожидания по правде,
поиск окна на экране. Живая проверка (стол + браузер) — scripts/test_card_accuracy.py."""
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from pokafer.eyes import sites
from pokafer.eyes.browser import find_on_screen
from pokafer.eyes.cards import ACCEPT_FRAC, CardReader, _parse_name, diff_fraction
from pokafer.eyes.slots import card_slots, slot_rect
from pokafer.eyes.truth import expected_cards, is_frozen

# ---- синтетические «карты»: белый прямоугольник на сукне, «ранг» — полоса,
#      «масть» — квадрат; форма та же, что у настоящих (углы просвечивают сукном)
FACE, FELT = (252, 252, 246), (20, 90, 60)
BLACK, RED, BLUE = (27, 31, 39), (196, 60, 60), (51, 80, 126)
W, H = 20, 28
WIN = (0, 0, 200, 100)  # окно = референс-вьюпорт пресета


def synth_card(rank: str, suit: str, felt=FELT) -> Image.Image:
    img = Image.new("RGB", (W, H), felt)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=3, fill=FACE)
    y = 4 if rank == "A" else 12                                        # «ранг»
    d.rectangle((3, y, 7, y + 4), fill=BLACK)
    d.rectangle((12, 20, 16, 24), fill=RED if suit in "hd" else BLACK)  # «масть»
    return img


def synth_back() -> Image.Image:
    return Image.new("RGB", (10, 14), BLUE)


@pytest.fixture
def eyes_dir(tmp_path, monkeypatch):
    """Реестр и пресет «t»: герой, борд из двух слотов, одно место; 200x100 px."""
    monkeypatch.setattr(sites, "EYES_DIR", tmp_path)
    (tmp_path / "sites.toml").write_text(
        '[presets.t]\ncalibration = "t.toml"\n\n[domains]\n"localhost" = "t"\n',
        encoding="utf-8")
    (tmp_path / "t.toml").write_text("""
[meta]
viewport_ref = [200, 100]
coords = "ref_px"
calibration_id = "id1"
seat_layouts = [["pos_top"], ["pos_tl", "pos_tr"]]

[hero]
cards = [[0.050000, 0.100000, 0.100000, 0.280000], [0.150000, 0.100000, 0.100000, 0.280000]]

[board]
slots = [[0.300000, 0.100000, 0.100000, 0.280000], [0.400000, 0.100000, 0.100000, 0.280000]]

[pot]
area = [0.300000, 0.500000, 0.200000, 0.100000]

[seat.pos_top]
cards = [[0.100000, 0.600000, 0.100000, 0.280000], [0.200000, 0.600000, 0.100000, 0.280000]]
backs = [[0.100000, 0.625000, 0.050000, 0.140000], [0.175000, 0.625000, 0.050000, 0.140000]]
""", encoding="utf-8")
    return tmp_path


@pytest.fixture
def preset(eyes_dir):
    return sites.load_preset("t")


# ---------- пресет ----------

def test_preset_slots_are_pixel_exact(preset):
    assert preset.slots("hero.cards", WIN) == [(10, 10, 20, 28), (30, 10, 20, 28)]
    assert preset.slots("board.slots", WIN) == [(60, 10, 20, 28), (80, 10, 20, 28)]
    assert preset.slots("seat.pos_top.backs", WIN) == [(20, 62, 10, 14), (35, 62, 10, 14)]
    assert preset.zone("pot.area", WIN) == (60, 50, 40, 10)


def test_preset_ref_px_keeps_pixels_and_fraction_scales(preset):
    big = (0, 0, 400, 200)
    assert preset.slots("hero.cards", big)[0] == (10, 10, 20, 28)    # px референса
    object.__setattr__(preset, "coords", "fraction")
    assert preset.slots("hero.cards", big)[0] == (20, 20, 40, 56)    # доли окна


def test_preset_rejects_malformed(eyes_dir):
    (eyes_dir / "t.toml").write_text('[hero]\ncards = [1.5, 0, 0.1, 0.2]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="вне диапазона"):
        sites.load_preset("t")
    (eyes_dir / "t.toml").write_text('[meta]\ncoords = "ref_px"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="viewport_ref"):
        sites.load_preset("t")


def test_preset_errors_name_the_node(preset):
    with pytest.raises(KeyError, match="hero.stack"):
        preset.zone("hero.stack", WIN)
    with pytest.raises(KeyError, match="не список слотов"):
        preset.slots("pot.area", WIN)


def test_preset_for_domain(preset):
    assert sites.preset_for_domain("localhost:8011").name == "t"
    assert sites.preset_for_domain("a.localhost").name == "t"
    with pytest.raises(KeyError, match="нет пресета"):
        sites.preset_for_domain("example.com")


def test_slot_enumeration(preset):
    paths = [s.path for s in card_slots(preset, WIN)]
    assert paths == ["hero.cards[0]", "hero.cards[1]", "board.slots[0]", "board.slots[1]",
                     "seat.pos_top.backs[0]", "seat.pos_top.backs[1]",
                     "seat.pos_top.cards[0]", "seat.pos_top.cards[1]"]
    assert slot_rect(preset, "board.slots[1]", WIN) == (80, 10, 20, 28)
    with pytest.raises(KeyError):
        slot_rect(preset, "board.slots[5]", WIN)
    with pytest.raises(KeyError):
        slot_rect(preset, "board.slots", WIN)


# ---------- метрика и читатель ----------

def test_diff_fraction_ignores_edge_and_felt_in_corners():
    a = synth_card("A", "s")
    assert diff_fraction(a, a) == 0.0
    assert diff_fraction(a, synth_card("A", "s", felt=(200, 30, 30))) == 0.0  # иное сукно
    assert diff_fraction(a, synth_card("A", "s").resize((W + 1, H))) == 1.0


def test_diff_fraction_separates_rank_and_suit():
    a = synth_card("A", "s")
    assert diff_fraction(a, synth_card("K", "s")) > ACCEPT_FRAC   # другой ранг
    assert diff_fraction(a, synth_card("A", "h")) > ACCEPT_FRAC   # другая масть


@pytest.fixture
def reader(eyes_dir):
    base = eyes_dir / "templates" / "t"
    base.mkdir(parents=True)
    for rank, suit in ("As", "Ah", "Ks"):
        synth_card(rank, suit).save(base / f"card_{rank}{suit}_20x28.png")
    synth_back().save(base / "back_10x14.png")
    Image.new("RGB", (W, H), FELT).save(base / "empty_board.slots_0.png")
    Image.new("RGB", (10, 14), FELT).save(base / "empty_seat.pos_top.backs_0.png")
    (base / "calibration_id").write_text("id1\n", encoding="utf-8")
    return CardReader(sites.load_preset("t"), templates_dir=eyes_dir / "templates")


def _frame(*placements):
    frame = Image.new("RGB", (200, 100), FELT)
    for x, img in placements:
        frame.paste(img, (x, 10))
    return frame


def test_reader_states(reader):
    frame = _frame((10, synth_card("A", "s")), (60, synth_back()),
                   (140, synth_card("Q", "d")))
    read = reader.read(frame, (10, 10, W, H), slot="board.slots[1]")
    assert (read.state, read.label, read.score) == ("face", "As", 0.0)
    assert reader.read(frame, (60, 10, 10, 14)).state == "back"
    assert reader.read(frame, (60, 10, 10, 14), slot="board.slots[0]").state == "back"
    assert reader.read(frame, (140, 10, W, H), slot="board.slots[1]").state == "unknown"
    assert reader.read(frame, (10, 50, W, H)).state == "unknown"    # сукно без эталона пустого
    assert reader.read(frame, (60, 50, W, H), slot="board.slots[0]").state == "empty"
    assert reader.labels() == ["Ah", "As", "Ks"] and reader.sizes() == [(20, 28)]


def test_reader_needs_empty_reference_per_slot(reader):
    frame = _frame((60, synth_card("A", "h")))
    got = reader.read(frame, (60, 10, W, H), slot="board.slots[0]")  # «пустой» слот занят
    assert (got.state, got.label) == ("face", "Ah")


def test_reader_reports_nearest_label(reader):
    frame = _frame((10, synth_card("K", "h")))      # Ks есть в шаблонах, Kh нет
    got = reader.read(frame, (10, 10, W, H))
    assert (got.state, got.nearest, got.label) == ("unknown", "Ks", None)


def test_reader_refuses_foreign_calibration(eyes_dir, reader):
    (eyes_dir / "templates" / "t" / "calibration_id").write_text("other\n", encoding="utf-8")
    with pytest.raises(ValueError, match="другой калибровки"):
        CardReader(sites.load_preset("t"), templates_dir=eyes_dir / "templates")


def test_reader_requires_templates(eyes_dir):
    with pytest.raises(FileNotFoundError, match="нет шаблонов"):
        CardReader(sites.load_preset("t"), templates_dir=eyes_dir / "nowhere")


def test_parse_name():
    assert _parse_name("card_As_58x82") == ("card", "As")
    assert _parse_name("back_34x48") == ("back", "")
    assert _parse_name("empty_board.slots_2") == ("empty", "board.slots[2]")
    assert _parse_name("empty_seat.pos_tl.cards_0") == ("empty", "seat.pos_tl.cards[0]")
    assert _parse_name("screenshot") is None


# ---------- правда ----------

def _truth(bots, board=("As", "Kd", "2c"), over=False):
    return {"hand_no": 3, "hand_over": over, "to_act": 1, "board": list(board),
            "hero": {"cards": ["Qh", "Jh"]}, "bots": bots}


def test_expected_cards_all_states(preset):
    bots = [{"folded": True, "cards": None},    # pos_tl: сбросил
            {"folded": False, "cards": None}]   # pos_tr: в игре, карты скрыты
    got = expected_cards(_truth(bots), preset)
    assert (got["hero.cards[0]"], got["hero.cards[1]"]) == ("Qh", "Jh")
    assert (got["board.slots[0]"], got["board.slots[1]"]) == ("As", "Kd")
    assert got["seat.pos_tl.cards[0]"] is None and got["seat.pos_tl.backs[0]"] is None
    assert got["seat.pos_tr.backs[0]"] == "back"
    assert got["seat.pos_tr.cards[0]"] == "covered"
    assert "seat.pos_top.cards[0]" not in got    # места нет при двух ботах


def test_expected_cards_showdown_and_empty_board(preset):
    bots = [{"folded": False, "cards": ["7c", "7d"]}]
    got = expected_cards(_truth(bots, board=(), over=True), preset)
    assert got["board.slots[0]"] is None and got["board.slots[1]"] is None
    assert (got["seat.pos_top.cards[0]"], got["seat.pos_top.cards[1]"]) == ("7c", "7d")
    assert "seat.pos_top.backs[0]" not in got    # рубашки под вскрытой картой не проверяются


def test_frozen_state():
    assert is_frozen({"hand_over": False, "to_act": 1})
    assert is_frozen({"hand_over": True, "to_act": None})
    assert not is_frozen({"hand_over": False, "to_act": 2})
    assert not is_frozen({})


# ---------- поиск окна на экране ----------

GEO = {"left": 0, "top": 0, "width": 800, "height": 600}


def _probe():
    probe = Image.new("RGB", (300, 200), (10, 10, 10))
    ImageDraw.Draw(probe).rectangle((120, 80, 180, 120), fill=(240, 180, 41))
    return probe


def test_find_on_screen_exact_placement():
    screen = Image.new("RGB", (800, 600), (30, 30, 30))
    assert find_on_screen(_probe(), screen, GEO) is None      # окна нет на экране
    screen.paste(_probe(), (137, 251))
    assert find_on_screen(_probe(), screen, GEO) == (137, 251)


def test_find_on_screen_rejects_changed_content():
    screen = Image.new("RGB", (800, 600), (30, 30, 30))
    screen.paste(_probe(), (137, 251))
    screen.putpixel((137 + 20, 251 + 150), (255, 255, 255))   # кадр на экране устарел
    assert find_on_screen(_probe(), screen, GEO) is None


# ---------- числа: разбор формата сумм ----------

@pytest.mark.parametrize("text, decimal, want", [
    ("199.75", ".", 199.75), ("0", ".", 0.0), ("5", ".", 5.0),
    ("12.5k", ".", 12500.0), ("10к", ".", 10000.0), ("1.2m", ".", 1200000.0), ("2.5М", ".", 2500000.0),
    ("$1,234.56", ".", 1234.56), ("12 345", ".", 12345.0), ("199.75.", ".", 199.75),
    ("12,50 €", ",", 12.5), ("1 234,56", ",", 1234.56), ("1.234,56", ",", 1234.56),
    ("1,234", ".", 1234.0), ("1,234", ",", 1.23),
    ("Банк: 19.5", ".", 19.5), ("Колл 1 200", ".", 1200.0), ("【0.5]", ".", 0.5),
    ("Max", ".", None), ("All-in", ".", None), ("Чек", ".", None), ("", ".", None),
    ("1.5Kbet", ".", 1.5),                     # суффикс внутри слова — не множитель
])
def test_parse_amount_formats(text, decimal, want):
    from pokafer.eyes.numbers import parse_amount
    got = parse_amount(text, decimal)
    assert (got is None and want is None) or (got is not None and want is not None and abs(got - want) < 1e-9)


def test_parse_amount_prefers_the_longest_number():
    """Подпись рядом распознаватель может прочесть с цифрами («6aHk: 19.5»):
    берётся самое длинное число строки, а не первое попавшееся."""
    from pokafer.eyes.numbers import parse_amount
    assert parse_amount("6aHk: 19.5") == 19.5
    assert parse_amount("Call 50, 2 players") == 50.0
    assert parse_amount("Bet 1 200") == 1200.0


def test_tighten_cuts_blank_margins():
    from pokafer.eyes.numbers import tighten
    img = Image.new("RGB", (80, 20), (20, 30, 40))
    ImageDraw.Draw(img).rectangle((30, 6, 35, 13), fill=(240, 240, 240))
    out = tighten(img)
    assert out.size == (6 + 2 * 2, 8 + 2 * 2)        # знак ± поле 2 px
    assert out.getpixel((0, 0)) == (20, 30, 40)       # фон вокруг знака сохранён
    assert tighten(Image.new("RGB", (30, 12), (20, 30, 40))).size == (30, 12)   # пусто — как было


# ---------- читатель чисел ----------

class _StubEngine:
    """Распознаватель без модели: отвечает заготовленным текстом."""

    def __init__(self, answer=("12.5", 0.99)):
        self.answer = answer
        self.calls = 0
        self.min_score = 0.6

    def read(self, image):
        self.calls += 1
        return self.answer


GOLD, PLATE = (240, 180, 41), (20, 30, 40)   # как фишка ставки и табличка на столе


def _number_preset(eyes_dir):
    from pokafer.eyes.sites import load_preset
    preset = load_preset("t")
    # зона фишки: фон — золото; зона стека: фон — тёмная табличка
    preset.zones["hero"]["bet"] = (0.6, 0.6, 0.2, 0.2)        # пиксели (120, 60, 40, 20)
    preset.zones["hero"]["stack"] = (0.1, 0.1, 0.3, 0.2)     # пиксели (20, 10, 60, 20)
    preset.zones["numbers"] = {"hero.bet": 180.0, "hero.stack": 26.0}
    preset.zones["numbers_format"] = {"decimal": "."}
    return preset


def _luma(color):
    return 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]


def _number_frame(chip_shown=True):
    frame = Image.new("RGB", (200, 100), (30, 110, 80))       # сукно
    frame.paste(Image.new("RGB", (40, 20), PLATE), (20, 10))  # табличка стека
    if chip_shown:
        frame.paste(Image.new("RGB", (40, 20), GOLD), (120, 60))
    return frame


def test_number_reader_states(eyes_dir):
    from pokafer.eyes.numbers import NumberReader
    preset = _number_preset(eyes_dir)
    # фоновые яркости зон — как в калибровке
    preset.zones["numbers"] = {"hero.bet": _luma(GOLD), "hero.stack": _luma(PLATE)}
    frame = _number_frame()
    engine = _StubEngine(("12.5", 0.99))
    reader = NumberReader(preset, engine=engine)
    got = reader.read(frame, "hero.bet")
    assert (got.state, got.value, got.text, got.score) == ("number", 12.5, "12.5", 0.99)
    low = NumberReader(preset, engine=_StubEngine(("12.5", 0.3))).read(frame, "hero.bet")
    assert (low.state, low.value) == ("low", 12.5)
    none = NumberReader(preset, engine=_StubEngine(("Чек", 0.9))).read(frame, "hero.stack")
    assert (none.state, none.value, none.text) == ("none", None, "Чек")
    assert reader.paths() == ["hero.bet", "hero.stack"]


def test_number_reader_hidden_element_never_calls_the_model(eyes_dir):
    from pokafer.eyes.numbers import NumberReader
    preset = _number_preset(eyes_dir)
    preset.zones["numbers"] = {"hero.bet": _luma(GOLD), "hero.stack": _luma(PLATE)}
    engine = _StubEngine()
    got = NumberReader(preset, engine=engine).read(_number_frame(chip_shown=False), "hero.bet")
    assert got.state == "empty" and engine.calls == 0     # фишки нет — сукно вместо золота


def test_number_reader_requires_zones(eyes_dir):
    from pokafer.eyes.numbers import NumberReader
    preset = _number_preset(eyes_dir)
    del preset.zones["numbers"]
    with pytest.raises(ValueError, match="зон чисел"):
        NumberReader(preset, engine=_StubEngine())
    preset.zones["numbers"] = {"hero.stack": 26.0}
    with pytest.raises(KeyError, match="hero.bet"):
        NumberReader(preset, engine=_StubEngine()).read(_number_frame(), "hero.bet")


def test_ocr_engine_reads_rendered_digits():
    """Настоящая модель (если установлена): строка цифр, отрисованная любым шрифтом."""
    pytest.importorskip("rapidocr")
    from pokafer.eyes.numbers import OcrEngine
    font_dir = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if not font_dir.exists():
        pytest.skip("нет шрифта для отрисовки образца")
    font = ImageFont.truetype(str(font_dir), 15)
    engine = OcrEngine()
    from pokafer.eyes.numbers import parse_amount
    for text, want in (("199.75", 199.75), ("0", 0.0), ("12.5k", 12500.0), ("$1,234.56", 1234.56)):
        img = Image.new("RGB", (90, 22), (20, 110, 70))
        ImageDraw.Draw(img).text((4, 2), text, font=font, fill=(250, 200, 40))
        got, score = engine.read(img)                  # широкая пустая зона: tighten срежет поля
        assert parse_amount(got) == want and score > 0.6, (text, got, score)


# ---------- зеркало глаз ----------

def test_mirror_publishes_only_to_subscribers():
    from pokafer.eyes import mirror
    session = "pytest-mirror"
    pub = mirror.MirrorPublisher(session, "http://table")
    try:
        assert pub.publish(mirror.encode({"n": 1})) is False     # страницу никто не открыл
        sub = mirror.MirrorSubscriber(session)
        try:
            sub.subscribe(True)
            assert pub.publish(mirror.encode({"n": 2}, b"\xff\xd8jpeg"))
            assert mirror.decode(sub.poll()) == ({"n": 2}, b"\xff\xd8jpeg")
            assert sub.poll() is None                            # нового не было
            pub.publish(mirror.encode({"n": 3}))
            pub.publish(mirror.encode({"n": 4}))
            assert mirror.decode(sub.poll())[0] == {"n": 4}      # читатель получает свежее
            assert pub.publish(bytes(mirror.SLOT_BYTES + 1)) is False   # не влезло — молча
            assert sub.age < 5
        finally:
            sub.subscribe(False)
            sub.close()
        assert pub.publish(mirror.encode({"n": 5})) is False     # подписчик ушёл
        assert any(s["session"] == session for s in mirror.sessions())
    finally:
        pub.close()
    assert not any(s["session"] == session for s in mirror.sessions())


def test_mirror_session_name_and_bad_memory():
    from pokafer.eyes import mirror
    assert mirror.memory_name("abc") == "pokafer_eyes_abc"
    with pytest.raises(FileNotFoundError):
        mirror.MirrorSubscriber("pytest-no-such-session")


# ---------- ядро рантайма: что глаза видят в кадре ----------

def test_perceive_reads_cards_and_numbers(reader):
    from pokafer.eyes.numbers import NumberReader
    from pokafer.eyes.perceive import perceive
    preset = reader.preset
    preset.zones["hero"]["stack"] = (0.75, 0.05, 0.15, 0.18)     # поле числа: (150, 5, 30, 18)
    preset.zones["numbers"] = {"hero.stack": _luma(FACE)}
    numbers = NumberReader(preset, engine=_StubEngine(("100", 0.98)))
    frame = _frame((10, synth_card("A", "s")))                    # первый слот героя
    frame.paste(synth_card("K", "s"), (30, 10))                   # второй слот героя (Ks есть в шаблонах)
    frame.paste(Image.new("RGB", (30, 18), FACE), (150, 5))       # «поле числа» с фоном, как в пресете
    p = perceive(frame, reader, numbers, seq=7)
    cards = {c.slot.path: c.reading for c in p.cards}
    assert (cards["hero.cards[0]"].state, cards["hero.cards[0]"].label) == ("face", "As")
    assert (cards["hero.cards[1]"].state, cards["hero.cards[1]"].label) == ("face", "Ks")
    assert cards["hero.cards[0]"].score == 0.0
    assert cards["board.slots[0]"].state == "empty"              # сукно с эталоном пустого
    assert cards["board.slots[1]"].state == "unknown"            # сукно без эталона пустого
    assert (p.numbers["hero.stack"].state, p.numbers["hero.stack"].value) == ("number", 100.0)
    assert set(p.timings) == {"cards", "numbers", "total"}
    state = p.to_state()                                        # для зеркала: чистый JSON
    assert state["seq"] == 7 and state["size"] == [200, 100]
    assert {"face", "unknown", "empty"} <= {c["state"] for c in state["cards"]}
    assert state["numbers"] == [{"path": "hero.stack", "rect": [150, 5, 30, 18], "state": "number",
                                 "value": 100.0, "text": "100", "score": 0.98}]
    json.dumps(state)


@pytest.mark.parametrize("c0, c1, b0, b1, want", [
    # открытые слоты места содержат рубашки и сукно: по отдельности «не распознато»
    ("unknown", "unknown", "back", "back", "hidden"),
    ("face", "face", "unknown", "unknown", "revealed"),
    ("empty", "unknown", "empty", "unknown", "none"),     # у сбросившего нет эталона для вторых слотов
    ("empty", "empty", "empty", "empty", "none"),
    ("unknown", "unknown", "empty", "unknown", "unclear"),
    ("face", "unknown", "back", "back", "unclear"),        # карта и рубашка одновременно
])
def test_seat_cards_summary(c0, c1, b0, b1, want):
    from types import SimpleNamespace
    from pokafer.eyes.perceive import _seat_cards
    readings = {f"seat.pos_top.cards[0]": c0, f"seat.pos_top.cards[1]": c1,
                f"seat.pos_top.backs[0]": b0, f"seat.pos_top.backs[1]": b1}
    cards = [SimpleNamespace(slot=SimpleNamespace(path=path, kind="seat"),
                             reading=SimpleNamespace(state=state, label="As" if state == "face" else None))
             for path, state in readings.items()]
    assert _seat_cards(cards)["pos_top"].state == want
