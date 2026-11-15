"""Оформление Merci: Material You поверх «жидкого стекла».

Три слоя глубины:

1. **Атмосфера** (``.merci-atmo``) — фон окна из мягких пятен палитры
   выбранного приложения. Окно непрозрачное на любом рабочем столе, поэтому
   стеклу нужно, что размывать, — и это наш собственный фон, а не размытие
   композитора, которого на X11 и в части Wayland-сессий нет.
2. **Стекло** (``.glass``) — немного крупных поверхностей: библиотека, шапка,
   док главного действия, панель «Окна», панель контейнера. Каждое размытие —
   отдельный проход отрисовки, поэтому мелкие детали стеклом не делаются.
3. **Содержимое M3** — тональные, почти непрозрачные поверхности без
   размытия: карточки, плитки, плашки.

Цвета в правилах — только через переменные. Шестнадцатеричные значения
появляются в одном месте: в блоке переменных, который строит
``palette_css`` из ролей ``merci.theme``. Мост к libadwaita
(``--accent-bg-color``, ``--popover-bg-color``…) объявлен на ``:root``,
поэтому меню, выпадающие списки, переключатели и диалоги остального
приложения перекрашиваются вместе с видом без отдельных правил.

Смена палитры плавная: у атмосферы два слоя (``slot-a``/``slot-b``) со
своими блоками переменных. Новая палитра пишется в невидимый слой, затем
верхний слой проявляется или гаснет — получается кроссфейд, а не щелчок.
"""

from __future__ import annotations

import colorsys

from gi.repository import Gdk, Gtk

from .. import theme

DEFAULT_SEED = "#4285f4"

# Слои атмосферы берут только эти роли — им и нужны свои копии в каждом слое.
_ATMO_ROLES = {
    "lowest": "surface_container_lowest",
    "surface": "surface",
    "pc": "primary_container",
    "tc": "tertiary_container",
    "sc": "secondary_container",
    "src": "source_color",
    "ter": "tertiary",
    "pri": "primary",
}

# Сила пятен атмосферы. В светлой теме контейнеры палитры пастельные, и без
# прибавки атмосфера выходит бледной — пятна там плотнее.
_ATMO_STRENGTH = {
    True: {"k1": 0.70, "k2": 0.40, "o1": 0.30, "o2": 0.22},
    False: {"k1": 1.00, "k2": 0.42, "o1": 0.48, "o2": 0.40},
}

# Пятна: (роль, сила, где центр, докуда спадает в долях farthest-side).
# Спад плавный, в четыре ступени до полной прозрачности — без «плато» и
# жёсткой границы, иначе пятно читается как отдельный круг-боке.
_BLOBS = (
    ("pc", "k1", "0% 0%", 62),
    ("tc", "k1", "100% 0%", 55),
    ("pri", "k2", "68% 108%", 55),
    ("sc", "k1", "0% 100%", 52),
    ("src", "o1", "24% 62%", 34),
    ("ter", "o2", "94% 48%", 30),
)


def _atmo_image() -> str:
    layers = []
    for role, strength, at, extent in _BLOBS:
        c = f"var(--a-{role})"
        layers.append(
            f"radial-gradient(circle farthest-side at {at}, "
            f"alpha({c}, var(--a-{strength})) 0%, "
            f"alpha({c}, var(--a-{strength}-m)) {extent * 0.38:.0f}%, "
            f"alpha({c}, var(--a-{strength}-l)) {extent * 0.72:.0f}%, "
            f"alpha({c}, 0) {extent}%)")
    layers.append("linear-gradient(180deg, var(--a-lowest), var(--a-surface))")
    return ",\n    ".join(layers)


# Производные токены: стекло, карточки, слои состояний. Выражены через роли,
# своих цветов не вводят. Тёмной и светлой теме нужны разные: в тёмной
# кромку «ловит» светлый край, в светлой — белый блик, тональная подкраска
# и мягкая цветная тень.
_DERIVED = {
    True: {
        "glass-fill": "alpha(var(--md-surface-container), 0.56)",
        "glass-fill-strong": "alpha(var(--md-surface-container-low), 0.88)",
        "glass-solid": "var(--md-surface-container)",
        "glass-solid-strong": "var(--md-surface-container-low)",
        "glass-sheen": "alpha(var(--md-on-surface), 0.06)",
        "glass-edge": "alpha(var(--md-on-surface), 0.09)",
        "glass-rim": "alpha(var(--md-on-surface), 0.24)",
        "glass-rim-side": "alpha(var(--md-on-surface), 0.13)",
        "glass-hi": "alpha(var(--md-on-surface), 0.10)",
        "glass-low": "alpha(var(--md-shadow), 0.22)",
        "glass-shadow": "alpha(var(--md-shadow), 0.50)",
        "glass-shadow-near": "alpha(var(--md-shadow), 0.24)",
        "dock-fill": "alpha(var(--md-surface-container-high), 0.84)",
        "card-fill": "alpha(var(--md-surface-container), 0.86)",
        "card-edge": "alpha(var(--md-outline-variant), 0.50)",
        "tile-fill": "alpha(var(--md-surface-container-high), 0.78)",
        "screen-off": "var(--md-surface-container-lowest)",
        "badge-fill": "alpha(var(--md-surface-container-highest), 0.92)",
        "icon-plate": "alpha(var(--md-surface-container-highest), 0.92)",
        "icon-plate-glyph": "var(--md-primary-container)",
        "icon-shadow": "alpha(var(--md-shadow), 0.35)",
        "state": "alpha(var(--md-on-surface), 0.08)",
        "state-strong": "alpha(var(--md-on-surface), 0.12)",
        "scrim-fill": "alpha(var(--md-scrim), 0.50)",
        "chip-fill": "alpha(var(--md-surface-container-highest), 0.80)",
        "field-fill": "alpha(var(--md-surface-container-highest), 0.92)",
        "group-fill": "alpha(var(--md-surface-container-highest), 0.55)",
        "dialog-rim": "alpha(white, 0.08)",
    },
    False: {
        "glass-fill": "alpha(var(--md-surface-container-low), 0.66)",
        "glass-fill-strong": "alpha(var(--md-surface-container-low), 0.90)",
        "glass-solid": "var(--md-surface-container-low)",
        "glass-solid-strong": "var(--md-surface-container-low)",
        "glass-sheen": "alpha(var(--md-surface-container-lowest), 0.60)",
        "glass-edge": "alpha(var(--md-outline-variant), 0.55)",
        "glass-rim": "alpha(var(--md-surface-container-lowest), 0.95)",
        "glass-rim-side": "alpha(var(--md-surface-container-lowest), 0.75)",
        "glass-hi": "alpha(var(--md-surface-container-lowest), 0.85)",
        "glass-low": "alpha(var(--md-outline-variant), 0.30)",
        "glass-shadow": "alpha(var(--md-primary), 0.20)",
        "glass-shadow-near": "alpha(var(--md-shadow), 0.06)",
        "dock-fill": "alpha(var(--md-surface-container-high), 0.82)",
        "card-fill": "alpha(var(--md-surface-container-lowest), 0.78)",
        "card-edge": "alpha(var(--md-outline-variant), 0.55)",
        "tile-fill": "alpha(var(--md-surface-container-lowest), 0.92)",
        "screen-off": "var(--md-surface-container-highest)",
        "badge-fill": "alpha(var(--md-surface-container-lowest), 0.94)",
        "icon-plate": "var(--md-surface-container-highest)",
        "icon-plate-glyph": "var(--md-primary)",
        "icon-shadow": "alpha(var(--md-shadow), 0.30)",
        "state": "alpha(var(--md-on-surface), 0.06)",
        "state-strong": "alpha(var(--md-on-surface), 0.10)",
        "scrim-fill": "alpha(var(--md-scrim), 0.32)",
        "chip-fill": "alpha(var(--md-surface-container-lowest), 0.85)",
        "field-fill": "var(--md-surface-container-lowest)",
        "group-fill": "alpha(var(--md-surface-container-lowest), 0.80)",
        "dialog-rim": "alpha(white, 0.80)",
    },
}

# Правила, которые у тем различаются формой, а не только цветом (тени из
# нескольких слоёв). Идут после общих правил и перекрывают их.
_THEME_CSS = {
    True: """
.app-icon.hero { box-shadow: 0 16px 40px -8px alpha(var(--md-primary), 0.55), 0 0 0 1px var(--glass-edge); }
""",
    False: """
.app-icon.hero {
  box-shadow: 0 14px 30px -10px alpha(var(--md-primary), 0.38), 0 2px 6px alpha(var(--md-shadow), 0.10),
              inset 0 1px 0 alpha(var(--md-surface-container-lowest), 0.9);
}
.glass { box-shadow: inset 0 1px 0 var(--glass-hi), inset 0 -1px 0 var(--glass-low),
                     0 18px 40px -14px var(--glass-shadow), 0 1px 3px var(--glass-shadow-near); }
.m3-card { box-shadow: inset 0 1px 0 var(--glass-hi), 0 10px 26px -16px alpha(var(--md-primary), 0.30); }
.screen-tile { box-shadow: 0 6px 16px -10px alpha(var(--md-primary), 0.35); }
""",
}

# Мост к libadwaita: её переменные берут роли M3.
_ADWAITA = {
    "accent-bg-color": "var(--md-primary)",
    "accent-fg-color": "var(--md-on-primary)",
    "accent-color": "var(--md-primary)",
    "destructive-bg-color": "var(--md-error)",
    "destructive-fg-color": "var(--md-on-error)",
    "destructive-color": "var(--md-error)",
    "window-bg-color": "var(--md-surface)",
    "window-fg-color": "var(--md-on-surface)",
    "view-bg-color": "var(--md-surface-container-low)",
    "view-fg-color": "var(--md-on-surface)",
    "headerbar-bg-color": "var(--md-surface-container)",
    "headerbar-fg-color": "var(--md-on-surface)",
    "headerbar-backdrop-color": "var(--md-surface)",
    "sidebar-bg-color": "var(--md-surface-container-low)",
    "sidebar-fg-color": "var(--md-on-surface)",
    "sidebar-backdrop-color": "var(--md-surface)",
    "secondary-sidebar-bg-color": "var(--md-surface-container)",
    "secondary-sidebar-fg-color": "var(--md-on-surface)",
    "card-bg-color": "var(--md-surface-container)",
    "card-fg-color": "var(--md-on-surface)",
    "dialog-bg-color": "var(--md-surface-container-high)",
    "dialog-fg-color": "var(--md-on-surface)",
    "popover-bg-color": "var(--md-surface-container-high)",
    "popover-fg-color": "var(--md-on-surface)",
    "thumbnail-bg-color": "var(--md-surface-container-high)",
    "thumbnail-fg-color": "var(--md-on-surface)",
}


def roles_for(seed: str | None, dark: bool) -> dict[str, str]:
    """Роли M3 для исходного цвета; кривой цвет — цвет по умолчанию."""
    try:
        return theme.scheme(seed or DEFAULT_SEED, dark)
    except ValueError:
        return theme.scheme(DEFAULT_SEED, dark)


def palette_css(seed: str | None, dark: bool) -> str:
    """Блок переменных на ``:root``: роли, производные токены, мост к Adwaita."""
    roles = roles_for(seed, dark)
    lines = [theme.css_variables(roles)]
    lines += [f"--{name}: {value};" for name, value in _DERIVED[dark].items()]
    lines += [f"--{name}: {value};" for name, value in _ADWAITA.items()]
    return ":root {\n" + "\n".join(lines) + "\n}\n"


def _hex_rgb(value: str) -> tuple[float, float, float] | None:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        return None
    try:
        return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def _calm(value: str, factor: float) -> str:
    """Тот же цвет с насыщенностью × factor (для тёплых палитр)."""
    rgb = _hex_rgb(value)
    if rgb is None:
        return value
    h, l, s = colorsys.rgb_to_hls(*rgb)
    r, g, b = colorsys.hls_to_rgb(h, l, s * factor)
    return "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in (r, g, b)))


def _warm(value: str) -> bool:
    rgb = _hex_rgb(value)
    if rgb is None:
        return False
    h, _l, s = colorsys.rgb_to_hls(*rgb)
    degrees = h * 360
    return s > 0.25 and (degrees < 50 or degrees > 330)


def atmosphere_css(slot: str, seed: str | None, dark: bool) -> str:
    """Свой блок переменных для одного слоя атмосферы (``slot-a``/``slot-b``).

    Тёплые палитры (красные, оранжевые) приглушаются: иначе вся атмосфера
    уходит в тревожный красный и спорит с цветом ошибки.
    """
    roles = roles_for(seed, dark)
    factor = 0.55 if _warm(roles["source_color"]) else 1.0
    lines = []
    for short, role in _ATMO_ROLES.items():
        value = roles[role]
        if factor < 1.0 and short not in ("lowest", "surface"):
            value = _calm(value, factor)
        lines.append(f"--a-{short}: {value};")
    for name, value in _ATMO_STRENGTH[dark].items():
        lines.append(f"--a-{name}: {value:.3f};")
        lines.append(f"--a-{name}-m: {value * 0.55:.3f};")
        lines.append(f"--a-{name}-l: {value * 0.18:.3f};")
    return f".merci-atmo.{slot} {{\n" + "\n".join(lines) + "\n}\n"


class StyleSheet:
    """Один ``Gtk.CssProvider`` на дисплей: переменные + правила.

    Провайдер общий для всех ``Shell`` на этом дисплее (в жизни он один):
    два провайдера с одинаковыми правилами только удвоили бы работу
    каскада. Приоритет — чуть выше приложения: правила вида должны
    перекрывать прежний CSS окна, но не пользовательский gtk.css.
    """

    _by_display: dict[int, "StyleSheet"] = {}

    @classmethod
    def for_display(cls, display: Gdk.Display) -> "StyleSheet":
        key = hash(display)
        sheet = cls._by_display.get(key)
        if sheet is None:
            sheet = cls(display)
            cls._by_display[key] = sheet
        return sheet

    def __init__(self, display: Gdk.Display) -> None:
        self.provider = Gtk.CssProvider()
        self.errors: list[str] = []
        self.provider.connect("parsing-error", self._on_error)
        Gtk.StyleContext.add_provider_for_display(
            display, self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self._text = ""

    def _on_error(self, _provider, section, error) -> None:
        where = section.to_string() if section is not None else "?"
        message = f"CSS вида: {where}: {error.message}"
        self.errors.append(message)
        print(message, flush=True)

    def load(self, seed: str | None, dark: bool,
             slot_a: tuple[str | None, bool], slot_b: tuple[str | None, bool]) -> None:
        text = (palette_css(seed, dark)
                + atmosphere_css("slot-a", *slot_a)
                + atmosphere_css("slot-b", *slot_b)
                + CSS + _THEME_CSS[dark])
        if text != self._text:
            self._text = text
            self.provider.load_from_string(text)




CSS = r"""
/* =================================================================== основа */
.merci-shell {
  color: var(--md-on-surface);
  background-color: var(--md-surface);
}
.merci-shell :focus:focus-visible {
  outline: 3px solid var(--md-primary);
  outline-offset: 2px;
}
.merci-shell label { caret-color: var(--md-primary); }

/* ================================================================ атмосфера */
.merci-atmo {
  background-color: var(--a-lowest);
  background-image:
    @ATMO@;
}
.merci-atmo.slot-b { transition: opacity 650ms ease-in-out; }
.merci-atmo.slot-b.faded { opacity: 0; }

/* ==================================================================== стекло */
/* Шкала радиусов: панели и карточки 24, плитки и группы 16, поля 12,
   чипы 8, кнопки и счётчики — капсула. */
.glass {
  background-color: var(--glass-fill);
  background-image: linear-gradient(165deg, var(--glass-sheen), alpha(var(--glass-sheen), 0) 42%);
  backdrop-filter: blur(18px) saturate(130%);
  border: 1px solid var(--glass-edge);
  border-top-color: var(--glass-rim);
  border-left-color: var(--glass-rim-side);
  box-shadow:
    inset 0 1px 0 var(--glass-hi),
    inset 0 -1px 0 var(--glass-low),
    0 18px 40px -12px var(--glass-shadow),
    0 2px 6px var(--glass-shadow-near);
}
.glass.strong { background-color: var(--glass-fill-strong); }
/* Док лежит над текстом страницы: подкраска плотнее, размытие сильнее. */
.glass.glass-dock { background-color: var(--dock-fill); backdrop-filter: blur(24px) saturate(140%); }
.sheet-notch { color: var(--glass-solid-strong); }

/* «Меньше прозрачности»: тот же облик, плотные тональные поверхности, без
   размытия и без бликов. */
.merci-shell.reduced .glass { backdrop-filter: none; background-color: var(--glass-solid); background-image: none; }
.merci-shell.reduced .glass.strong,
.merci-shell.reduced .glass.glass-dock { background-color: var(--glass-solid-strong); }
.merci-shell.reduced .panel-scrim { backdrop-filter: none; }

.glass-sidebar { border-radius: 24px; }
.glass-header { border-radius: 24px; min-height: 60px; }
.glass-dock { border-radius: 999px; }
.glass-panel { border-radius: 24px; }
/* Цвет текста панели задаётся явно: «язычок» над ней (SheetBin) берёт свою
   заливку из CSS color, и без этого правила все подписи панели наследовали
   бы цвет стекла — заголовки строк сливались с фоном. */
.glass-sheet { border-radius: 24px; color: var(--md-on-surface); }

/* ==================================================================== кнопки */
button.m3 {
  min-height: 40px; padding: 0 18px; border-radius: 999px;
  font-weight: 700; border: none; box-shadow: none;
  background-image: none;
  transition: background-color 160ms ease, box-shadow 160ms ease, border-radius 220ms ease;
}
button.m3.small { min-height: 36px; padding: 0 14px; font-size: 13px; }
button.m3:active { border-radius: 14px; }
button.m3.filled { background-color: var(--md-primary); color: var(--md-on-primary); }
button.m3.filled:hover { background-image: linear-gradient(alpha(var(--md-on-primary), 0.08), alpha(var(--md-on-primary), 0.08)); box-shadow: 0 2px 8px -2px alpha(var(--md-shadow), 0.4); }
button.m3.tonal { background-color: var(--md-secondary-container); color: var(--md-on-secondary-container); }
button.m3.tonal:hover { background-image: linear-gradient(alpha(var(--md-on-secondary-container), 0.08), alpha(var(--md-on-secondary-container), 0.08)); }
button.m3.outlined { background-color: transparent; color: var(--md-on-surface); border: 1px solid var(--md-outline); padding: 0 17px; }
button.m3.outlined:hover { background-color: var(--state); }
button.m3.text { background-color: transparent; color: var(--md-primary); padding: 0 12px; }
button.m3.text:hover { background-color: alpha(var(--md-primary), 0.10); }
button.m3.danger { background-color: transparent; color: var(--md-error); border: 1px solid alpha(var(--md-error), 0.55); padding: 0 17px; }
button.m3.danger:hover { background-color: alpha(var(--md-error), 0.10); }
/* Недоступные кнопки — одного вида, какого бы стиля они ни были. */
/* Недоступные: подложка 12 % и подпись 38 % on-surface (норма M3). Свой
   «полупрозрачный» фильтр Adwaita снят, иначе кнопка почти исчезает. */
button.m3:disabled { background-color: alpha(var(--md-on-surface), 0.12); color: alpha(var(--md-on-surface), 0.38); border-color: transparent; background-image: none; box-shadow: none; filter: none; opacity: 1; }
button.m3:disabled > *, button.m3:disabled label, button.m3:disabled image { filter: none; opacity: 1; color: alpha(var(--md-on-surface), 0.38); }
button.m3.text:disabled { background-color: transparent; }

button.icon-btn {
  min-width: 40px; min-height: 40px; padding: 0; border-radius: 999px;
  background-color: transparent; color: var(--md-on-surface-variant); box-shadow: none; border: none;
}
button.icon-btn:hover { background-color: var(--state-strong); color: var(--md-on-surface); }
button.icon-btn.tonal { background-color: var(--md-secondary-container); color: var(--md-on-secondary-container); }
button.icon-btn.tonal:hover { background-image: linear-gradient(alpha(var(--md-on-secondary-container), 0.08), alpha(var(--md-on-secondary-container), 0.08)); }
button.icon-btn.field-btn { min-width: 36px; min-height: 36px; }
/* Галочка у поля размера «заряжена», когда значение изменено и ждёт Enter. */
button.icon-btn.tonal.armed { background-color: var(--md-primary); color: var(--md-on-primary); }
button.icon-btn:disabled { color: alpha(var(--md-on-surface), 0.38); background-color: alpha(var(--md-on-surface), 0.12); filter: none; opacity: 1; }
button.icon-btn:disabled > image { filter: none; opacity: 1; }
button.icon-btn:disabled:not(.tonal) { background-color: transparent; }
menubutton.icon-btn { padding: 0; min-width: 0; min-height: 0; }
menubutton.icon-btn > button {
  min-width: 40px; min-height: 40px; padding: 0; border-radius: 999px;
  background-color: transparent; color: var(--md-on-surface-variant); box-shadow: none; border: none;
}
menubutton.icon-btn > button:hover, menubutton.icon-btn > button:checked { background-color: var(--state-strong); color: var(--md-on-surface); }
menubutton.icon-btn.small > button { min-width: 36px; min-height: 36px; }

/* ================================================================= библиотека */
.lib-head { padding: 16px 10px 8px 20px; }
.lib-title { font-size: 21px; font-weight: 800; letter-spacing: -0.2px; }
.lib-count { font-size: 12px; color: var(--md-on-surface-variant); }
list.lib-list { background: none; }
list.lib-list > row {
  border-radius: 16px; padding: 8px 10px; margin: 2px 0;
  background-color: transparent; color: var(--md-on-surface);
  transition: background-color 160ms ease;
}
list.lib-list > row:hover { background-color: var(--state); }
/* Показанная запись — класс .current (выбор идёт по нажатию, не по фокусу). */
list.lib-list > row.current {
  background-color: var(--md-secondary-container);
  color: var(--md-on-secondary-container);
  box-shadow: inset 0 1px 0 var(--glass-hi);
}
list.lib-list > row:focus:focus-visible { outline-offset: -3px; }
list.lib-list > row.current .lib-ver { color: var(--md-on-secondary-container); }
list.lib-list.compact > row { padding: 6px; border-radius: 16px; }
.lib-name { font-weight: 700; font-size: 14px; }
.lib-ver { font-size: 12px; color: var(--md-on-surface-variant); font-feature-settings: "tnum"; }
.lib-skeleton { padding: 2px 0 8px 0; }
.ghost-row { padding: 8px 10px; }
.ghost-row.s2 { opacity: 0.66; }
.ghost-row.s3 { opacity: 0.36; }
.ghost-icon { min-width: 44px; min-height: 44px; border-radius: 14px; background-color: alpha(var(--md-on-surface), 0.07); }
.ghost-bar { border-radius: 999px; background-color: alpha(var(--md-on-surface), 0.08); }
.lib-empty { font-size: 13px; color: var(--md-on-surface-variant); padding: 14px 16px; }
.arch-chip {
  font-size: 11px; font-weight: 800; letter-spacing: 0.3px;
  padding: 0 7px; border-radius: 8px; min-height: 18px;
}
.arch-chip.arm { background-color: var(--md-secondary-container); color: var(--md-on-secondary-container); }
list.lib-list > row.current .arch-chip.arm { background-color: alpha(var(--md-on-secondary-container), 0.14); }
.arch-chip.native { border: 1px solid var(--md-outline); color: var(--md-on-surface-variant); padding: 0 6px; }
list.lib-list > row.current .arch-chip.native { border-color: alpha(var(--md-on-secondary-container), 0.7); color: var(--md-on-secondary-container); }
.run-badge {
  min-height: 22px; padding: 0 8px 0 6px; border-radius: 999px;
  background-color: var(--md-primary); color: var(--md-on-primary);
  font-size: 12px; font-weight: 800; font-feature-settings: "tnum";
}
/* Счётчик на значке в рейке: крупнее и с обводкой цветом рейки — так он
   отделяется от значка под ним. */
.run-dot {
  min-width: 18px; min-height: 18px; border-radius: 999px;
  background-color: var(--md-primary); color: var(--md-on-primary);
  box-shadow: 0 0 0 2px var(--glass-solid);
  font-size: 11px; font-weight: 800;
}
button.add-apk { min-height: 48px; }
/* Обновления — спокойная строка-кнопка: тональная, без тревожного цвета.
   Это новость, а не ошибка. */
button.updates-row {
  min-height: 44px; padding: 0 14px; border-radius: 16px; border: none; box-shadow: none;
  background-color: alpha(var(--md-secondary-container), 0.55); color: var(--md-on-surface);
}
button.updates-row:hover { background-color: var(--md-secondary-container); }
/* Знак обновлений — стрелка вверх в круге, а не шестерёнка «Настроек». */
.updates-glyph {
  min-width: 18px; min-height: 18px; border-radius: 999px;
  border: 2px solid var(--md-primary); color: var(--md-primary);
}
button.updates-btn .updates-glyph { min-width: 20px; min-height: 20px; }
/* Счётчик на значке в рейке — как число открытых окон на значках приложений. */
.run-dot.updates-count { margin: 2px; }
.updates-text { font-weight: 700; font-size: 13.5px; }
.updates-chevron { color: var(--md-on-surface-variant); }
/* Точка «есть обновления» на кнопке меню и на значке в рейке. */
.update-dot {
  min-width: 10px; min-height: 10px; border-radius: 999px; margin: 5px;
  background-color: var(--md-primary); box-shadow: 0 0 0 2px var(--glass-solid);
}

/* =================================================================== значки */
.app-icon {
  border-radius: 14px;
  background-color: var(--icon-plate);
  color: var(--md-on-surface-variant);
  box-shadow: inset 0 1px 0 var(--glass-hi);
}
.app-icon.light-glyph { background-color: var(--icon-plate-glyph); }
.app-icon image { -gtk-icon-shadow: 0 1px 2px var(--icon-shadow); }
.app-icon.placeholder { background-color: var(--md-secondary-container); color: var(--md-on-secondary-container); }
.app-icon.placeholder image { -gtk-icon-shadow: none; }
.app-icon.small { border-radius: 10px; }
.app-icon.hero { border-radius: 32px; }

/* ===================================================================== шапка */
.glass-header windowcontrols { margin: 0 2px; }
/* Цель нажатия 36 px; видимый кружок рисует тема на значке внутри. */
.glass-header windowcontrols button { min-width: 36px; min-height: 36px; padding: 0; }
.crumb-name { font-weight: 800; font-size: 15px; }
/* Карандаш у названия — тихий: глиф цвета подписи, слой при наведении. */
button.rename-btn { min-width: 36px; min-height: 36px; color: var(--md-on-surface-variant); }

/* ===================================================================== остров */
/* Своей подложки у острова нет: он лежит на стекле шапки. */
.island { padding: 0; background: none; border: none; }
button.island-status {
  min-height: 40px; padding: 0 12px; border-radius: 999px;
  background: none; box-shadow: none; border: none; color: var(--md-on-surface);
}
button.island-status:hover { background-color: var(--state); }
.island-title { font-weight: 800; font-size: 13px; }
.island-sub { font-size: 12.5px; color: var(--md-on-surface-variant); }
.island-div { min-width: 1px; background-color: alpha(var(--md-outline-variant), 0.9); margin: 12px 4px; }
button.island-chip, menubutton.island-chip > button {
  min-height: 40px; padding: 0 12px; border-radius: 999px;
  background-color: transparent; color: var(--md-on-surface);
  font-weight: 700; font-size: 12.5px; border: none; box-shadow: none;
}
menubutton.island-chip { padding: 0; min-height: 0; }
button.island-chip:hover, menubutton.island-chip > button:hover { background-color: var(--state-strong); }
button.island-chip:checked { background-color: var(--md-secondary-container); color: var(--md-on-secondary-container); }
/* Стрелка — второстепенное действие: прозрачная, со слоем наведения.
   Заливка primary в окне только у «Запустить» и у выбранного. */
button.island-expand {
  min-width: 40px; min-height: 40px; border-radius: 999px; padding: 0;
  background-color: transparent; color: var(--md-on-surface-variant); border: none; box-shadow: none;
}
button.island-expand:hover { background-color: var(--state-strong); color: var(--md-on-surface); }
button.island-expand:checked { background-color: var(--md-secondary-container); color: var(--md-on-secondary-container); }

.status-dot {
  min-width: 10px; min-height: 10px; border-radius: 999px;
  background-color: var(--md-primary);
  box-shadow: 0 0 0 3px alpha(var(--md-primary), 0.24), 0 0 10px alpha(var(--md-primary), 0.7);
}
/* Состояние не одним цветом: у остановленной сессии точка полая, у
   неподготовленной — квадратная, неясное — серое и мигает несколько раз
   (не бесконечно: под точкой стекло, и каждый кадр пересчитывал бы размытие). */
.status-dot.asleep { background-color: transparent; box-shadow: inset 0 0 0 2px var(--md-outline); }
.status-dot.unprepared { background-color: var(--md-error); box-shadow: 0 0 0 3px alpha(var(--md-error), 0.24); border-radius: 3px; }
.status-dot.unknown { background-color: var(--md-outline); box-shadow: none; animation: dot-pulse 1.4s ease-in-out 4; }
@keyframes dot-pulse { 0% { opacity: 1; } 50% { opacity: 0.25; } 100% { opacity: 1; } }
/* Контейнер в пути (поднимается, гаснет, перезапускается) — индикатор хода. */
.status-spin { color: var(--md-primary); }

/* ========================================================== панель контейнера */
.panel-scrim { background-color: var(--scrim-fill); backdrop-filter: blur(6px); }
.panel-title { font-size: 22px; font-weight: 800; letter-spacing: -0.3px; }
.panel-sub { font-size: 13px; color: var(--md-on-surface-variant); }
.group-cap { font-size: 12px; font-weight: 800; letter-spacing: 1px; color: var(--md-primary); margin: 0 6px; }
.set-group { border-radius: 16px; padding: 4px 0; background-color: var(--group-fill); }
.set-row { padding: 10px 14px; min-height: 44px; }
.set-row + .set-row { border-top: 1px solid alpha(var(--md-outline-variant), 0.55); }
.set-icon { color: var(--md-on-surface-variant); }
.set-title { font-size: 14px; font-weight: 650; }
.set-sub { font-size: 12px; color: var(--md-on-surface-variant); }
.set-row.alert .set-icon, .set-row.alert .set-sub { color: var(--md-error); }
.stop-hint { font-size: 12px; color: var(--md-on-surface-variant); margin: 0 6px; }
.stop-hint image { color: var(--md-on-surface-variant); }
/* Недоступные быстрые переключатели острова, поле размера, сегменты. */
button.island-chip:disabled, menubutton.island-chip > button:disabled { color: alpha(var(--md-on-surface), 0.38); filter: none; opacity: 1; }
.segmented:disabled { opacity: 0.6; }

entry.field {
  min-height: 38px; border-radius: 12px; padding: 0 12px;
  background-color: var(--field-fill); color: var(--md-on-surface);
  border: 1px solid var(--md-outline-variant); box-shadow: none; outline: none;
  font-feature-settings: "tnum";
}
entry.field:focus-within { border-color: var(--md-primary); box-shadow: inset 0 0 0 1px var(--md-primary); }
entry.field:disabled { color: alpha(var(--md-on-surface), 0.5); }

.segmented { border-radius: 999px; background-color: var(--field-fill); padding: 3px; border: 1px solid alpha(var(--md-outline-variant), 0.7); }
.segmented > button {
  min-height: 36px; padding: 0 12px; border-radius: 999px; background: none; box-shadow: none; border: none;
  color: var(--md-on-surface-variant); font-weight: 700; font-size: 12.5px;
}
.segmented > button:hover { background-color: var(--state); }
/* Выбранный сегмент: заливка secondary-container, жирнее и с галочкой. */
.segmented > button:checked {
  background-color: var(--md-secondary-container); color: var(--md-on-secondary-container);
  font-weight: 800; box-shadow: inset 0 0 0 1px alpha(var(--md-on-secondary-container), 0.18);
}

.fps-menu { padding: 6px; }
.fps-menu button { min-height: 36px; border-radius: 12px; padding: 0 14px; font-weight: 600; }

/* ============================================================= переключатель */
/* Один вид и в окне, и в диалогах логики: включённый — primary с ручкой
   on-primary, выключенный — контурная дорожка outline с ручкой outline (M3). */
.merci-shell switch, dialog switch {
  background-color: var(--md-surface-container-highest); background-image: none;
  box-shadow: inset 0 0 0 2px var(--md-outline); border: none; color: transparent;
}
.merci-shell switch > slider, dialog switch > slider {
  background-color: var(--md-outline); background-image: none; box-shadow: none; border: none;
}
.merci-shell switch:checked, dialog switch:checked { background-color: var(--md-primary); box-shadow: none; }
.merci-shell switch:checked > slider, dialog switch:checked > slider { background-color: var(--md-on-primary); }
.merci-shell switch:hover:not(:checked) > slider, dialog switch:hover:not(:checked) > slider { background-color: var(--md-on-surface-variant); }
.merci-shell switch:disabled, dialog switch:disabled { opacity: 0.38; }

/* ===================================================================== герой */
.hero-over { font-size: 12px; font-weight: 800; letter-spacing: 1.3px; color: var(--md-primary); }
.hero-title { font-size: 40px; font-weight: 800; letter-spacing: -0.8px; }
.hero-sub { font-size: 15px; color: var(--md-on-surface-variant); font-feature-settings: "tnum"; }
.chip {
  min-height: 30px; padding: 0 11px; border-radius: 8px; font-size: 13px; font-weight: 650;
  background-color: var(--chip-fill); color: var(--md-on-surface-variant);
  border: 1px solid alpha(var(--md-outline-variant), 0.7);
}
.chip.accent { background-color: var(--md-secondary-container); color: var(--md-on-secondary-container); border-color: transparent; }
.chip.mono label { font-family: monospace; font-size: 12.5px; }

/* ==================================================================== плашки */
/* Подкрашенное стекло: предупреждение — лёгкий оттенок ошибки с рамкой,
   ошибка — то же на ступень плотнее, сведения — тональная secondary. */
.notice {
  border-radius: 24px; padding: 10px 10px 10px 18px; min-height: 56px;
  color: var(--md-on-surface);
  background-color: alpha(var(--md-error-container), 0.35);
  border: 1px solid alpha(var(--md-error), 0.40);
}
.notice .notice-glyph { color: var(--md-error); }
.notice .notice-text { font-weight: 650; }
.notice button.m3 { background-color: alpha(var(--md-error), 0.16); color: var(--md-on-surface); }
.notice button.m3:hover { background-color: alpha(var(--md-error), 0.24); }
.notice.tone-error { background-color: alpha(var(--md-error-container), 0.58); border-color: alpha(var(--md-error), 0.55); }
.notice.tone-info { background-color: alpha(var(--md-secondary-container), 0.55); border-color: alpha(var(--md-outline-variant), 0.7); }
.notice.tone-info .notice-glyph { color: var(--md-primary); }
.notice.tone-info button.m3 { background-color: var(--md-secondary-container); color: var(--md-on-secondary-container); }

/* ====================================================================== окна */
.windows-panel { padding: 20px; }
.section-title { font-size: 22px; font-weight: 800; letter-spacing: -0.3px; }
.section-sub { font-size: 13px; color: var(--md-on-surface-variant); }
.op-strip {
  border-radius: 16px; padding: 10px 16px 8px 14px;
  background-color: alpha(var(--md-primary-container), 0.75); color: var(--md-on-primary-container);
}
.op-text { font-weight: 700; font-size: 13px; }
.op-strip .wavy { color: var(--md-primary); }
.windows-status { padding: 22px 12px 16px 12px; }
.windows-status .status-title { font-weight: 800; font-size: 15px; }
.windows-status .status-text { font-size: 13px; color: var(--md-on-surface-variant); }
.windows-status .status-glyph { color: var(--md-on-surface-variant); }
.windows-status.error .status-glyph { color: var(--md-error); }
.status-details { color: var(--md-on-surface-variant); font-size: 13px; font-weight: 650; }
.details-text { font-family: monospace; font-size: 12px; font-weight: 400; color: var(--md-on-surface-variant); }

flowbox.tiles > flowboxchild { padding: 0; background: none; border-radius: 16px; outline: none; }
.screen-tile {
  border-radius: 16px; padding: 6px 6px 8px 6px;
  background-color: var(--tile-fill);
  border: 1px solid var(--card-edge);
  transition: box-shadow 200ms ease, border-color 200ms ease;
}
.screen-tile:hover { border-color: alpha(var(--md-primary), 0.6); box-shadow: 0 14px 30px -16px alpha(var(--md-primary), 0.9); }
/* Открытое окно — рамка 2 px primary и тональный фон, а не оттенок рамки. */
.screen-tile.st-open {
  border: 2px solid var(--md-primary); padding: 5px 5px 7px 5px;
  background-color: alpha(var(--md-primary-container), 0.32);
}
.screen-tile.st-dead { border-color: alpha(var(--md-error), 0.55); }

.screen {
  border-radius: 10px;
  background-color: var(--md-surface-container-high);
  box-shadow: inset 0 0 0 1px alpha(var(--md-on-surface), 0.07);
}
.st-open .screen {
  background-color: var(--md-primary-container);
  background-image: radial-gradient(circle farthest-side at 28% 18%, alpha(var(--md-primary), 0.50), alpha(var(--md-primary), 0) 70%),
                    linear-gradient(135deg, var(--md-primary-container), var(--md-secondary-container));
}
.st-background .screen {
  background-image: linear-gradient(135deg, alpha(var(--md-primary-container), 0.55), alpha(var(--md-secondary-container), 0.30));
}
.st-idle .screen, .st-absent .screen {
  background-image: radial-gradient(circle farthest-side at 50% 42%, alpha(var(--md-secondary-container), 0.9), alpha(var(--md-secondary-container), 0) 75%);
}
.st-off .screen { background-color: var(--screen-off); }
.st-booting .screen, .st-busy .screen {
  background-image: linear-gradient(135deg, alpha(var(--md-primary-container), 0.55), alpha(var(--md-secondary-container), 0.20));
}
/* Штриховка — только у «не отвечает»: это ошибка. «Другая сборка» —
   сведения: тональный фон без узора. */
.st-dead .screen {
  background-image: repeating-linear-gradient(135deg, alpha(var(--md-error), 0.11) 0px, alpha(var(--md-error), 0.11) 8px, alpha(var(--md-error), 0) 8px, alpha(var(--md-error), 0) 16px);
  box-shadow: inset 0 0 0 1.5px alpha(var(--md-error), 0.65);
}
.st-foreign .screen { background-color: alpha(var(--md-tertiary-container), 0.75); }
/* Значок приложения есть на экране всегда; состояние — подачей. */
.st-open .screen-art { box-shadow: 0 12px 22px -8px var(--glass-shadow); }
.st-background .screen-art { opacity: 0.5; filter: saturate(30%); }
.screen-art.dim { filter: grayscale(1); opacity: 0.45; }
.screen-art.faint { filter: saturate(40%); opacity: 0.7; }
.art-ring { color: var(--md-primary); }
.art-hatch {
  border-radius: 14px;
  background-image: repeating-linear-gradient(135deg, alpha(var(--md-error), 0.55) 0px, alpha(var(--md-error), 0.55) 3px, alpha(var(--md-error), 0) 3px, alpha(var(--md-error), 0) 7px);
  box-shadow: inset 0 0 0 1.5px alpha(var(--md-error), 0.8);
}
.art-badge {
  min-width: 20px; min-height: 20px; border-radius: 999px;
  background-color: var(--md-primary); color: var(--md-on-primary);
  box-shadow: 0 0 0 2px var(--md-surface-container-high);
}
.art-badge.faint { background-color: var(--md-surface-container-highest); color: var(--md-on-surface); }
.art-badge.hatch { background-color: var(--md-error); color: var(--md-on-error); }
.st-foreign .art-badge { background-color: var(--md-tertiary); color: var(--md-on-tertiary); box-shadow: 0 0 0 2px var(--md-tertiary-container); }

.screen-badge {
  min-height: 24px; padding: 0 9px 0 7px; border-radius: 8px; font-size: 11.5px; font-weight: 800;
  background-color: var(--badge-fill); color: var(--md-on-surface);
}
.st-open .screen-badge { background-color: var(--md-primary); color: var(--md-on-primary); }
.st-dead .screen-badge { background-color: var(--md-error); color: var(--md-on-error); }
.st-foreign .screen-badge { background-color: var(--md-tertiary); color: var(--md-on-tertiary); }

button.screen-btn {
  min-width: 36px; min-height: 36px; border-radius: 999px; padding: 0; border: none;
  background-color: var(--badge-fill); color: var(--md-on-surface);
  box-shadow: 0 4px 10px -4px var(--glass-shadow);
}
button.screen-btn:hover { background-image: linear-gradient(var(--state-strong), var(--state-strong)); }
button.screen-btn.primary { background-color: var(--md-primary); color: var(--md-on-primary); }
button.screen-btn.danger { background-color: var(--md-error); color: var(--md-on-error); }
button.screen-btn:disabled { opacity: 0.5; }

.tile-title { font-weight: 800; font-size: 14px; }
.tile-role { font-size: 11px; font-weight: 800; padding: 1px 8px; border-radius: 8px;
  color: var(--md-on-surface-variant); background-color: alpha(var(--md-on-surface), 0.07); }
.tile-role.main { color: var(--md-on-secondary-container); background-color: var(--md-secondary-container); }
.tile-state { font-size: 12px; color: var(--md-on-surface-variant); }
.st-dead .tile-state { color: var(--md-error); }

button.ghost-tile {
  border-radius: 16px; border: 2px dashed alpha(var(--md-outline), 0.6);
  background-color: alpha(var(--md-surface-container), 0.25); color: var(--md-on-surface-variant);
  box-shadow: none; padding: 16px 8px;
}
button.ghost-tile:hover { background-color: var(--state); color: var(--md-primary); border-color: var(--md-primary); }
.ghost-title { font-weight: 800; font-size: 14px; }
.ghost-sub { font-size: 12px; }

/* =================================================================== карточки */
.m3-card {
  border-radius: 24px; padding: 18px 20px;
  background-color: var(--card-fill);
  border: 1px solid var(--card-edge);
}
.card-title { font-size: 16px; font-weight: 800; }
.kv-key { font-size: 12px; color: var(--md-on-surface-variant); }
.kv-val { font-size: 14px; font-feature-settings: "tnum"; }
.kv-sep { min-height: 1px; background-color: alpha(var(--md-outline-variant), 0.6); }
/* «Сборка в контейнере» — строка состояния: спокойный фон, смысл — в
   значке-кружке. */
.build-box {
  border-radius: 16px; padding: 10px 14px 10px 10px;
  background-color: alpha(var(--md-surface-container-high), 0.9); color: var(--md-on-surface);
}
.build-mark {
  min-width: 30px; min-height: 30px; border-radius: 999px;
  background-color: alpha(var(--md-on-surface), 0.08); color: var(--md-on-surface-variant);
}
.build-box.ok .build-mark { background-color: var(--md-primary); color: var(--md-on-primary); }
.build-box.other .build-mark { background-color: var(--md-tertiary); color: var(--md-on-tertiary); }
.build-box .kv-val { font-weight: 650; }
dropdown.field > button {
  min-height: 38px; border-radius: 12px; background-color: var(--field-fill);
  color: var(--md-on-surface); border: 1px solid var(--md-outline-variant); box-shadow: none;
}

/* ======================================================================== док */
.glass-dock { padding: 6px; }
.dock-title { font-weight: 800; font-size: 14px; }
.dock-sub { font-size: 12px; color: var(--md-on-surface-variant); }
button.fab {
  min-height: 56px; min-width: 176px; border-radius: 22px; padding: 0 26px 0 22px;
  background-color: var(--md-primary); color: var(--md-on-primary);
  background-image: linear-gradient(180deg, alpha(var(--md-on-primary), 0.10), alpha(var(--md-on-primary), 0) 55%);
  font-size: 16px; font-weight: 800; border: none;
  box-shadow: 0 10px 24px -6px alpha(var(--md-primary), 0.70), inset 0 1px 0 alpha(var(--md-on-primary), 0.25);
  transition: border-radius 260ms ease, background-color 200ms ease, box-shadow 200ms ease;
}
button.fab:hover { border-radius: 28px; background-image: linear-gradient(alpha(var(--md-on-primary), 0.08), alpha(var(--md-on-primary), 0.08)); }
button.fab:active { border-radius: 16px; }
button.fab:disabled { background-color: alpha(var(--md-on-surface), 0.12); color: alpha(var(--md-on-surface), 0.45); box-shadow: none; background-image: none; }
/* Занятая кнопка — один вид, доступна она или нет: primary-container и
   индикатор. Кольцо фокуса (outline со смещением) с этим видом не путается. */
button.fab.busy, button.fab.busy:disabled {
  background-color: var(--md-primary-container); color: var(--md-on-primary-container);
  background-image: none; box-shadow: none; font-size: 14.5px; opacity: 1; filter: none;
}
button.fab.busy:disabled label { color: var(--md-on-primary-container); filter: none; opacity: 1; }

/* =========================================================== сообщение снизу */
.snackbar {
  border-radius: 16px; padding: 6px 6px 6px 18px; min-height: 48px;
  background-color: var(--md-inverse-surface); color: var(--md-inverse-on-surface);
  box-shadow: 0 10px 28px -8px alpha(var(--md-shadow), 0.55);
}
.snackbar button { color: var(--md-inverse-primary); font-weight: 800; background: none; border-radius: 999px; min-height: 36px; padding: 0 14px; box-shadow: none; }
.snackbar button:hover { background-color: alpha(var(--md-inverse-on-surface), 0.10); }

/* ========================================================== пустая библиотека */
.dropzone { padding: 44px 52px 40px 52px; }
.dropzone-ring {
  min-width: 104px; min-height: 104px; border-radius: 32px;
  border: 2px dashed var(--md-primary);
  background-color: alpha(var(--md-primary-container), 0.55); color: var(--md-primary);
}
.welcome-title { font-size: 26px; font-weight: 800; letter-spacing: -0.4px; }
.welcome-sub { font-size: 14px; color: var(--md-on-surface-variant); }
""".replace("@ATMO@", _atmo_image()) + r"""
/* ==================================================================== диалоги */
/* Обычные диалоги libadwaita (вопросы, настройки, мастер подготовки,
   журналы) — в стиле главного окна. Код диалогов чужой, поэтому только CSS:
   форма и поверхность как у панелей, капсулы-кнопки по ролям M3, строки
   настроек — тональные карточки. Правила ограничены узлом dialog, чтобы не
   задеть ничего в самом окне. */
/* Тот же слой, что под панелью контейнера: затемнение scrim и одно размытие. */
floating-sheet > dimming, bottom-sheet > dimming {
  background-color: var(--scrim-fill); backdrop-filter: blur(6px);
}
/* Поверхность — как у стеклянных панелей: тональная surface-container-high
   с лёгкой подкраской primary сверху, блик по верхней кромке и тень. Поля
   сверху и снизу (56 px к собственным ~33 px слоя: верх не выше нижней
   кромки шапки + 12) не дают высокому
   диалогу (мастер) наползать на шапку: он листается внутри. Короткие
   вопросы стоят по центру, как и прежде. */
floating-sheet > sheet, dialog-host > dialog.alert sheet {
  border-radius: 24px;
  background-color: var(--md-surface-container-high); color: var(--md-on-surface);
  background-image: linear-gradient(170deg, alpha(var(--md-primary), 0.07), alpha(var(--md-primary), 0) 46%);
  outline: 1px solid var(--glass-edge); outline-offset: -1px;
  box-shadow: inset 0 1px 0 var(--dialog-rim), 0 24px 56px -16px var(--glass-shadow), 0 2px 8px var(--glass-shadow-near);
}
floating-sheet > sheet { margin-top: 56px; margin-bottom: 56px; }
bottom-sheet > sheet {
  background-color: var(--md-surface-container-high); color: var(--md-on-surface);
  box-shadow: inset 0 1px 0 var(--dialog-rim);
}
bottom-sheet > sheet, bottom-sheet > sheet > outline { border-top-left-radius: 24px; border-top-right-radius: 24px; }
dialog-host > dialog.alert sheet .body-scrolled-window > viewport { border-top-left-radius: 24px; border-top-right-radius: 24px; }
dialog :focus:focus-visible { outline: 3px solid var(--md-primary); outline-offset: 2px; }

/* Шапка диалога — прозрачная, заголовок тем же начертанием, что в окне. */
dialog headerbar { background: none; box-shadow: none; color: var(--md-on-surface); }
dialog headerbar .title { font-weight: 800; font-size: 15px; }
dialog .title-1 { font-weight: 800; letter-spacing: -0.4px; }
dialog .title-2 { font-weight: 800; letter-spacing: -0.3px; }

/* -- вопросы и ошибки (Adw.AlertDialog) -- */
dialog.alert .message-area { padding-top: 28px; }
dialog.alert .message-area > .heading-bin label { font-size: 20px; font-weight: 800; letter-spacing: -0.3px; }
dialog.alert .message-area > .body { font-size: 14px; color: var(--md-on-surface-variant); }
.merci-dialog-text { font-size: 14px; color: var(--md-on-surface-variant); line-height: 1.45; }
dialog.alert .response-area { padding: 20px 24px 24px 24px; border-spacing: 10px; }
dialog.alert .response-area > button {
  min-height: 40px; padding: 0 22px; border-radius: 999px; border: none; box-shadow: none;
  font-weight: 700; background-image: none;
  background-color: var(--md-secondary-container); color: var(--md-on-secondary-container);
}
dialog.alert .response-area > button:hover { background-image: linear-gradient(var(--state), var(--state)); }
dialog.alert .response-area > button.suggested-action { background-color: var(--md-primary); color: var(--md-on-primary); }
/* Разрушительный ответ — тональный на error-container, без сплошной
   заливки: среди альтернатив он не должен быть самым громким пятном, а
   главный ответ (suggested) остаётся залитым primary. */
dialog.alert .response-area > button.destructive-action {
  background-color: alpha(var(--md-error-container), 0.55); color: var(--md-on-error-container);
  box-shadow: inset 0 0 0 1px alpha(var(--md-error), 0.35);
}
dialog.alert .response-area > button:disabled { background-color: alpha(var(--md-on-surface), 0.08); color: alpha(var(--md-on-surface), 0.40); }
/* Кольцо фокуса — снаружи капсулы, со смещением: его не спутать с заливкой. */
dialog.alert .response-area > button:focus:focus-visible { outline: 3px solid var(--md-primary); outline-offset: 3px; }

/* -- кнопки в остальных диалогах -- */
dialog button.suggested-action, dialog button.pill.suggested-action {
  background-color: var(--md-primary); color: var(--md-on-primary); border-radius: 999px; box-shadow: none;
}
dialog button.destructive-action {
  background-color: alpha(var(--md-error-container), 0.55); color: var(--md-on-error-container);
  border-radius: 999px; box-shadow: inset 0 0 0 1px alpha(var(--md-error), 0.35);
}
dialog button.pill { border-radius: 999px; min-height: 40px; padding: 0 22px; font-weight: 700; }
dialog button.pill:not(.suggested-action):not(.destructive-action):not(.flat) {
  background-color: var(--md-secondary-container); color: var(--md-on-secondary-container);
}
/* Значок-заголовок окна обновлений. */
.merci-update-icon { color: var(--md-primary); }

/* -- строки настроек: тональные карточки -- */
dialog preferencespage > scrolledwindow > viewport > clamp > box { margin: 20px 16px 24px 16px; border-spacing: 22px; }
/* Содержимое ниже края — нижняя кромка растворяется (24 px), как у панели
   контейнера: видно, что дальше есть ещё, и диалог не выглядит обрезанным. */
dialog scrolledwindow > undershoot.bottom {
  background-image: linear-gradient(to top, var(--md-surface-container-high), alpha(var(--md-surface-container-high), 0) 24px);
  box-shadow: none;
}
dialog scrolledwindow > undershoot.top {
  background-image: linear-gradient(to bottom, var(--md-surface-container-high), alpha(var(--md-surface-container-high), 0) 16px);
  box-shadow: none;
}
dialog preferencesgroup > box > box.header .title,
dialog preferencesgroup > box > box.header .heading {
  font-size: 12px; font-weight: 800; letter-spacing: 1px; color: var(--md-primary);
  text-transform: uppercase;
}
dialog preferencesgroup > box > box.header .description { font-size: 12.5px; color: var(--md-on-surface-variant); }
dialog list.boxed-list, dialog list.boxed-list-separate > row {
  border-radius: 16px; background-color: var(--group-fill); color: var(--md-on-surface);
  box-shadow: none; outline: 1px solid alpha(var(--md-outline-variant), 0.45); outline-offset: -1px;
}
dialog list.boxed-list > row { border-bottom: 1px solid alpha(var(--md-outline-variant), 0.55); }
dialog list.boxed-list > row:last-child { border-bottom: none; }
dialog list.boxed-list > row:first-child { border-top-left-radius: 16px; border-top-right-radius: 16px; }
dialog list.boxed-list > row:last-child { border-bottom-left-radius: 16px; border-bottom-right-radius: 16px; }
dialog list.boxed-list > row:not(:selected).activatable:hover { background-color: var(--state); }
/* Только подпись-заголовок: у строки «title» — ещё и коробка с обеими
   подписями, и без «label» жирным стал бы и пояснительный текст. */
dialog row label.title { font-weight: 650; }
dialog row label.subtitle { font-size: 12.5px; font-weight: 400; color: var(--md-on-surface-variant); opacity: 1; }
dialog row.expander list.nested { background-color: alpha(var(--md-on-surface), 0.03); }
dialog row.entry text, dialog row.entry > box.header > .editable-area { font-feature-settings: "tnum"; }
dialog row.entry.focused { outline: 2px solid var(--md-primary); outline-offset: -2px; }
dialog dropdown > button, dialog row.combo > box > box.suffixes dropdown > button { border-radius: 12px; }
dialog popover.menu > contents { border-radius: 16px; }

/* -- мастер подготовки и журналы -- */
row.merci-step { min-height: 56px; }
row.merci-step label.title { font-weight: 650; }
row.merci-step .prefixes { color: var(--md-primary); }
row.merci-step image.success { color: var(--md-primary); }
row.merci-step image.error { color: var(--md-error); }
row.merci-step image.dim-label { color: var(--md-outline); opacity: 1; }
.merci-percent {
  font-size: 44px; font-weight: 800; letter-spacing: -1.2px; color: var(--md-primary);
  font-feature-settings: "tnum";
}
progressbar.merci-progress > trough {
  min-height: 10px; border-radius: 999px; background-color: alpha(var(--md-primary), 0.16);
}
progressbar.merci-progress > trough > progress {
  min-height: 10px; border-radius: 999px; background-color: var(--md-primary);
}
textview.merci-log, textview.merci-log > text {
  font-family: monospace; font-size: 12px;
  background-color: var(--md-surface-container-lowest); color: var(--md-on-surface-variant);
}
textview.merci-log { border-radius: 12px; }

/* -- мелкие метки: «нужен пароль», «847 МБ» -- */
.merci-chip {
  min-height: 22px; padding: 0 8px; border-radius: 8px;
  font-size: 11.5px; font-weight: 800; font-feature-settings: "tnum";
  background-color: var(--chip-fill); color: var(--md-on-surface-variant);
  border: 1px solid alpha(var(--md-outline-variant), 0.7);
}
.merci-chip.accent { background-color: var(--md-secondary-container); color: var(--md-on-secondary-container); border-color: transparent; }
.merci-chip.warn { background-color: alpha(var(--md-error-container), 0.75); color: var(--md-on-error-container); border-color: alpha(var(--md-error), 0.45); }
"""
