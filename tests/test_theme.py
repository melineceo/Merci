"""Проверка цветового движка по эталону matugen.

Эталон снят заранее (tests/fixtures/theme_reference.json), поэтому
тестам matugen не нужен. Значки для проверки исходного цвета лежат в
.dev/design/icons/ — вне репозитория; без них эти проверки пропускаются,
а синтетические картинки проверяются всегда.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zlib
import struct

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from merci import theme  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "theme_reference.json")
ICONS = os.path.join(ROOT, ".dev", "design", "icons")

# Допуск из задания: ±2 на канал. Сейчас совпадение точное, но допуск
# оставлен как договорённость, а не как подгонка.
TOLERANCE = 2


def _channels(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _distance(a: str, b: str) -> int:
    return max(abs(x - y) for x, y in zip(_channels(a), _channels(b)))


def _hue(color: str) -> float:
    return theme.Hct(theme._parse(color)).hue


def _png(width: int, height: int, pixel) -> bytes:
    """Простой RGBA PNG без фильтров — для проверок без чужих файлов."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixel(x, y))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw)))
            + chunk(b"IEND", b""))


class ReferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with open(FIXTURE, encoding="utf-8") as fh:
            cls.ref = json.load(fh)

    def test_role_names_match_matugen(self) -> None:
        self.assertEqual(tuple(self.ref["roles"]), theme.ROLES)

    def test_fixture_is_broad_enough(self) -> None:
        seeds = {e["seed"] for e in self.ref["schemes"]}
        variants = {e["variant"] for e in self.ref["schemes"]}
        self.assertGreaterEqual(len(seeds), 16)
        self.assertTrue({"tonal_spot", "vibrant", "expressive", "content", "fidelity",
                         "neutral", "monochrome"} <= variants)

    def test_schemes_match_reference(self) -> None:
        roles = self.ref["roles"]
        worst = 0
        failures = []
        for entry in self.ref["schemes"]:
            for mode in ("dark", "light"):
                got = theme.scheme(entry["seed"], mode == "dark", entry["variant"], entry["contrast"])
                for role, want in zip(roles, entry[mode]):
                    d = _distance(got[role], want)
                    worst = max(worst, d)
                    if d > TOLERANCE:
                        failures.append(f"{entry['seed']} {entry['variant']} c={entry['contrast']} "
                                        f"{mode} {role}: {got[role]} != {want}")
        self.assertEqual(failures, [], "\n".join(failures[:20]))
        # Сейчас совпадение побитовое; если это изменится — пусть будет видно.
        self.assertLessEqual(worst, TOLERANCE)

    def test_tonal_matches_roles(self) -> None:
        # Тона ролей без поправок на контраст — прямо тона палитр.
        for seed in ("#4285f4", "#e53b2b", "#808080"):
            light = theme.scheme(seed, False)
            dark = theme.scheme(seed, True)
            self.assertEqual(theme.tonal(seed, "primary", 40), light["primary"])
            self.assertEqual(theme.tonal(seed, "primary", 90), light["primary_container"])
            self.assertEqual(theme.tonal(seed, "neutral", 6), dark["surface"])
            self.assertEqual(theme.tonal(seed, "neutral_variant", 30), dark["surface_variant"])
            self.assertEqual(theme.tonal(seed, "error", 80), dark["error"])
            self.assertEqual(theme.tonal(seed, "tertiary", 40, "vibrant"),
                             theme.scheme(seed, False, "vibrant")["tertiary"])
        self.assertEqual(theme.tonal("#4285f4", "neutral", 0), "#000000")
        self.assertEqual(theme.tonal("#4285f4", "neutral", 100), "#ffffff")


class ApiTest(unittest.TestCase):
    def test_all_variants_produce_every_role(self) -> None:
        for variant in theme.VARIANTS:
            for dark in (True, False):
                roles = theme.scheme("#20a38e", dark, variant)
                self.assertEqual(tuple(sorted(roles)), theme.ROLES)
                for value in roles.values():
                    self.assertRegex(value, r"^#[0-9a-f]{6}$")

    def test_seed_spellings(self) -> None:
        base = theme.scheme("#4285f4", True)
        self.assertEqual(theme.scheme("4285F4", True), base)
        self.assertEqual(theme.scheme("#4285f4", True, "scheme-tonal-spot"), base)
        self.assertEqual(theme.scheme("#fff", True), theme.scheme("#ffffff", True))

    def test_bad_input(self) -> None:
        with self.assertRaises(ValueError):
            theme.scheme("#12345", True)
        with self.assertRaises(ValueError):
            theme.scheme("#4285f4", True, "pastel")
        with self.assertRaises(ValueError):
            theme.tonal("#4285f4", "accent", 50)

    def test_cached_result_is_a_copy(self) -> None:
        first = theme.scheme("#6f28bf", True)
        first["primary"] = "#000000"
        self.assertNotEqual(theme.scheme("#6f28bf", True)["primary"], "#000000")

    def test_css_variables(self) -> None:
        css = theme.css_variables({"primary_container": "#d8e2ff", "on_primary": "#ffffff"})
        self.assertEqual(css, "--md-primary-container: #d8e2ff;\n--md-on-primary: #ffffff;")
        self.assertEqual(theme.css_variables({"primary": "#adc6ff"}, "--app-"), "--app-primary: #adc6ff;")

    def test_import_does_not_pull_gtk(self) -> None:
        code = "import sys; import merci.theme; print(any(m == 'gi' or m.startswith('gi.') for m in sys.modules))"
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True)
        self.assertEqual(out.stdout.strip(), "False")


class SeedTest(unittest.TestCase):
    def test_colorless_pixels_fall_back(self) -> None:
        self.assertEqual(theme.seed_from_pixels([], "#123456"), "#123456")
        grays = [(v, v, v, 255) for v in range(0, 256, 3)] * 10
        self.assertEqual(theme.seed_from_pixels(grays), "#4285f4")
        transparent = [(255, 0, 0, 0)] * 100
        self.assertEqual(theme.seed_from_pixels(transparent, "#00ff00"), "#00ff00")

    def test_dominant_color_wins(self) -> None:
        pixels = [(229, 59, 43)] * 900 + [(32, 163, 142)] * 100 + [(250, 250, 250)] * 500
        seed = theme.seed_from_pixels(pixels)
        self.assertLess(theme._difference_degrees(_hue(seed), _hue("#e53b2b")), 15)

    def test_translucent_edges_are_ignored(self) -> None:
        pixels = [(51, 94, 253, 255)] * 400 + [(255, 180, 0, 90)] * 2000
        seed = theme.seed_from_pixels(pixels)
        self.assertLess(theme._difference_degrees(_hue(seed), _hue("#335efd")), 15)

    def test_png_without_gtk(self) -> None:
        def pixel(x: int, y: int) -> bytes:
            if (x - 48) ** 2 + (y - 48) ** 2 < 40 ** 2:
                return bytes((111, 40, 191, 255))
            return bytes((0, 0, 0, 0))

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "icon.png")
            with open(path, "wb") as fh:
                fh.write(_png(96, 96, pixel))
            pixels = list(theme._png_pixels(path))
            seed = theme.seed_from_pixels(pixels)
            self.assertLess(theme._difference_degrees(_hue(seed), _hue("#6f28bf")), 15)
            self.assertLess(theme._difference_degrees(_hue(theme.seed_from_png(path)), _hue("#6f28bf")), 15)

    def test_unreadable_file_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broken = os.path.join(tmp, "broken.png")
            with open(broken, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\nnot really")
            self.assertEqual(theme.seed_from_png(broken, "#abcdef"), "#abcdef")
            self.assertEqual(theme.seed_from_png(os.path.join(tmp, "missing.png"), "#abcdef"), "#abcdef")


@unittest.skipUnless(os.path.isdir(ICONS), "нет .dev/design/icons")
class IconTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with open(FIXTURE, encoding="utf-8") as fh:
            cls.images = json.load(fh)["images"]

    def _check(self, seed_of) -> None:
        for name, want in self.images.items():
            with self.subTest(icon=name):
                got = seed_of(os.path.join(ICONS, name))
                if name in ("apk.png", "delta.png"):
                    # Бесцветные значки: matugen отдаёт fallback — и мы тоже.
                    self.assertEqual(got, want)
                else:
                    self.assertLess(theme._difference_degrees(_hue(got), _hue(want)), 15,
                                    f"{name}: {got} против {want}")

    def test_icons(self) -> None:
        self._check(theme.seed_from_png)

    def test_icons_without_gtk(self) -> None:
        self._check(lambda path: theme.seed_from_pixels(theme._png_pixels(path)))


if __name__ == "__main__":
    unittest.main()
