"""Цвета Material You для приложений: палитра из значка, как в Android.

Это перенос material-color-utilities (той же библиотеки, что стоит за
динамическим цветом Android и за matugen) на чистый Python. Внешняя
утилита здесь не годится: Merci — флатпак и должна давать одинаковые
цвета на любом дистрибутиве, а сам алгоритм небольшой и детерминированный.

Порядок работы такой же, как в оригинале:

* цвет переводится в HCT — тон CAM16 (hue), насыщенность (chroma) и
  светлота L* из CIELAB (tone). Светлота в HCT напрямую задаёт контраст,
  поэтому роли можно строить «тон 40 на тоне 90» и получать читаемый текст
  при любом оттенке;
* из исходного цвета вариант схемы строит шесть тональных палитр;
* каждая роль (primary, surface_container, …) — тон одной из палитр,
  подправленный так, чтобы выдерживать нужный контраст со своим фоном.

Числа и порядок операций сохранены как в оригинале намеренно: эталоном
служит matugen, и любое «упрощение» формулы сдвигает цвета на единицу-две
в канале — незаметно глазу, но тогда сверка с эталоном теряет смысл.
"""

from __future__ import annotations

import math
from functools import lru_cache
from itertools import accumulate
from typing import Callable, Iterable


# ---------------------------------------------------------------------------
# Цвет: sRGB, XYZ, L*
# ---------------------------------------------------------------------------

_SRGB_TO_XYZ = (
    (0.41233895, 0.35762064, 0.18051042),
    (0.2126, 0.7152, 0.0722),
    (0.01932141, 0.11916382, 0.95034478),
)
_WHITE_POINT_D65 = (95.047, 100.0, 108.883)


def _round(x: float) -> int:
    """Округление как Math.round в Java: половина — вверх.

    Встроенный round() в Python округляет половину к чётному, и на тонах
    вроде 59.5 это меняло ветку в правилах контраста.
    """
    return math.floor(x + 0.5)


def _fma(a: float, b: float, c: float) -> float:
    fma = getattr(math, "fma", None)  # есть с Python 3.13
    return fma(a, b, c) if fma is not None else a * b + c


def _clamp(lo: float, hi: float, x: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _sanitize_degrees(d: float) -> float:
    return d % 360.0


def _difference_degrees(a: float, b: float) -> float:
    return 180.0 - abs(abs(a - b) - 180.0)


def _signum(x: float) -> float:
    return -1.0 if x < 0 else 1.0 if x > 0 else 0.0


def _linearized(component: int) -> float:
    """Канал sRGB 0..255 → линейная яркость 0..100."""
    n = component / 255.0
    if n <= 0.040449936:
        return n / 12.92 * 100.0
    return ((n + 0.055) / 1.055) ** 2.4 * 100.0


def _true_delinearized(linear: float) -> float:
    """Линейная яркость 0..100 → канал sRGB 0..255 без округления."""
    n = linear / 100.0
    if n <= 0.0031308:
        d = n * 12.92
    else:
        d = 1.055 * n ** (1.0 / 2.4) - 0.055
    return d * 255.0


def _delinearized(linear: float) -> int:
    return int(_clamp(0, 255, _round(_true_delinearized(linear))))


def _argb(r: int, g: int, b: int) -> int:
    return (255 << 24) | ((r & 255) << 16) | ((g & 255) << 8) | (b & 255)


def _rgb(argb: int) -> tuple[int, int, int]:
    return (argb >> 16) & 255, (argb >> 8) & 255, argb & 255


def _argb_from_linrgb(lin: tuple[float, float, float] | list[float]) -> int:
    return _argb(_delinearized(lin[0]), _delinearized(lin[1]), _delinearized(lin[2]))


_LAB_E = 216.0 / 24389.0
_LAB_KAPPA = 24389.0 / 27.0


def _lab_f(t: float) -> float:
    if t > _LAB_E:
        return t ** (1.0 / 3.0)
    return (_LAB_KAPPA * t + 16.0) / 116.0


def _lab_invf(ft: float) -> float:
    ft3 = ft * ft * ft
    if ft3 > _LAB_E:
        return ft3
    return (116.0 * ft - 16.0) / _LAB_KAPPA


def _y_from_lstar(lstar: float) -> float:
    return 100.0 * _lab_invf((lstar + 16.0) / 116.0)


def _lstar_from_y(y: float) -> float:
    return _lab_f(y / 100.0) * 116.0 - 16.0


def _lstar_from_argb(argb: int) -> float:
    r, g, b = _rgb(argb)
    y = 0.2126 * _linearized(r) + 0.7152 * _linearized(g) + 0.0722 * _linearized(b)
    # Умножение со сложением без промежуточного округления, как в
    # эталоне: там у чистого чёрного L* выходит −2·10⁻¹⁶, а не ровно 0.
    # Разница не косметическая — «тон меньше нуля» правила контраста
    # считают недопустимым, и текст на чёрном контейнере (content,
    # fidelity) становится белым вместо серого.
    return _fma(116.0, _lab_f(y / 100.0), -16.0)


def _argb_from_lstar(lstar: float) -> int:
    c = _delinearized(_y_from_lstar(lstar))
    return _argb(c, c, c)


def _lab_from_argb(argb: int) -> tuple[float, float, float]:
    r, g, b = (_linearized(c) for c in _rgb(argb))
    m = _SRGB_TO_XYZ
    x = (m[0][0] * r + m[0][1] * g + m[0][2] * b) / _WHITE_POINT_D65[0]
    y = (m[1][0] * r + m[1][1] * g + m[1][2] * b) / _WHITE_POINT_D65[1]
    z = (m[2][0] * r + m[2][1] * g + m[2][2] * b) / _WHITE_POINT_D65[2]
    fx, fy, fz = _lab_f(x), _lab_f(y), _lab_f(z)
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


# ---------------------------------------------------------------------------
# CAM16 в стандартных условиях просмотра
# ---------------------------------------------------------------------------

def _make_viewing_conditions() -> dict[str, float | tuple[float, float, float]]:
    """Условия просмотра sRGB по умолчанию: белая точка D65, фон L* 50.

    Считаются один раз при загрузке модуля — это десяток операций.
    """
    white = _WHITE_POINT_D65
    adapting_luminance = (200.0 / math.pi) * _y_from_lstar(50.0) / 100.0
    background_lstar = 50.0
    surround = 2.0
    rw = white[0] * 0.401288 + white[1] * 0.650173 + white[2] * -0.051461
    gw = white[0] * -0.250268 + white[1] * 1.204414 + white[2] * 0.045854
    bw = white[0] * -0.002079 + white[1] * 0.048952 + white[2] * 0.953127
    f = 0.8 + surround / 10.0
    if f >= 0.9:
        c = 0.59 + (0.69 - 0.59) * ((f - 0.9) * 10.0)
    else:
        c = 0.525 + (0.59 - 0.525) * ((f - 0.8) * 10.0)
    d = f * (1.0 - (1.0 / 3.6) * math.exp((-adapting_luminance - 42.0) / 92.0))
    d = _clamp(0.0, 1.0, d)
    nc = f
    rgb_d = (d * (100.0 / rw) + 1.0 - d, d * (100.0 / gw) + 1.0 - d, d * (100.0 / bw) + 1.0 - d)
    k = 1.0 / (5.0 * adapting_luminance + 1.0)
    k4 = k * k * k * k
    k4f = 1.0 - k4
    fl = k4 * adapting_luminance + 0.1 * k4f * k4f * (5.0 * adapting_luminance) ** (1.0 / 3.0)
    n = _y_from_lstar(background_lstar) / white[1]
    z = 1.48 + math.sqrt(n)
    nbb = 0.725 / n ** 0.2
    factors = [(fl * rgb_d[i] * w / 100.0) ** 0.42 for i, w in enumerate((rw, gw, bw))]
    rgb_a = [400.0 * x / (x + 27.13) for x in factors]
    aw = (2.0 * rgb_a[0] + rgb_a[1] + 0.05 * rgb_a[2]) * nbb
    return {
        "n": n, "aw": aw, "nbb": nbb, "ncb": nbb, "c": c, "nc": nc,
        "rgb_d": rgb_d, "fl": fl, "fl_root": fl ** 0.25, "z": z,
    }


_VC = _make_viewing_conditions()
_VC_N: float = _VC["n"]  # type: ignore[assignment]
_VC_AW: float = _VC["aw"]  # type: ignore[assignment]
_VC_NBB: float = _VC["nbb"]  # type: ignore[assignment]
_VC_C: float = _VC["c"]  # type: ignore[assignment]
_VC_NC: float = _VC["nc"]  # type: ignore[assignment]
_VC_RGB_D: tuple[float, float, float] = _VC["rgb_d"]  # type: ignore[assignment]
_VC_FL: float = _VC["fl"]  # type: ignore[assignment]
_VC_Z: float = _VC["z"]  # type: ignore[assignment]
_T_INNER_COEFF = 1.0 / (1.64 - 0.29 ** _VC_N) ** 0.73
_ALPHA_COEFF = (1.64 - 0.29 ** _VC_N) ** 0.73


def _cam16_hue_chroma(argb: int) -> tuple[float, float]:
    """Тон и насыщенность CAM16 цвета. Остальные корреляты нам не нужны."""
    r, g, b = (_linearized(c) for c in _rgb(argb))
    x = 0.41233895 * r + 0.35762064 * g + 0.18051042 * b
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = 0.01932141 * r + 0.11916382 * g + 0.95034478 * b
    rc = 0.401288 * x + 0.650173 * y - 0.051461 * z
    gc = -0.250268 * x + 1.204414 * y + 0.045854 * z
    bc = -0.002079 * x + 0.048952 * y + 0.953127 * z
    adapted = []
    for value, d in zip((rc, gc, bc), _VC_RGB_D):
        rd = d * value
        af = (_VC_FL * abs(rd) / 100.0) ** 0.42
        adapted.append(_signum(rd) * 400.0 * af / (af + 27.13))
    ra, ga, ba = adapted
    a = (11.0 * ra + -12.0 * ga + ba) / 11.0
    bb = (ra + ga - 2.0 * ba) / 9.0
    u = (20.0 * ra + 20.0 * ga + 21.0 * ba) / 20.0
    p2 = (40.0 * ra + 20.0 * ga + ba) / 20.0
    hue = math.degrees(math.atan2(bb, a))
    if hue < 0:
        hue += 360.0
    elif hue >= 360:
        hue -= 360.0
    ac = p2 * _VC_NBB
    j = 100.0 * (ac / _VC_AW) ** (_VC_C * _VC_Z)
    hue_prime = hue + 360.0 if hue < 20.14 else hue
    e_hue = 0.25 * (math.cos(math.radians(hue_prime) + 2.0) + 3.8)
    p1 = 50000.0 / 13.0 * e_hue * _VC_NC * _VC_NBB
    t = p1 * math.hypot(a, bb) / (u + 0.305)
    alpha = t ** 0.9 * _ALPHA_COEFF
    chroma = alpha * math.sqrt(j / 100.0)
    return hue, chroma


# ---------------------------------------------------------------------------
# Решатель HCT: цвет с заданными тоном, насыщенностью и светлотой
# ---------------------------------------------------------------------------

_SCALED_DISCOUNT_FROM_LINRGB = (
    (0.001200833568784504, 0.002389694492170889, 0.0002795742885861124),
    (0.0005891086651375999, 0.0029785502573438758, 0.0003270666104008398),
    (0.00010146692491640572, 0.0005364214359186694, 0.0032979401770712076),
)
_LINRGB_FROM_SCALED_DISCOUNT = (
    (1373.2198709594231, -1100.4251190754821, -7.278681089101213),
    (-271.815969077903, 559.6580465940733, -32.46047482791194),
    (1.9622899599665666, -57.173814538844006, 308.7233197812385),
)
_Y_FROM_LINRGB = (0.2126, 0.7152, 0.0722)
# Линейные яркости посередине между соседними значениями канала: на этих
# плоскостях цвет в 8 битах меняется, поэтому бисекция идёт по ним.
_CRITICAL_PLANES = tuple(
    ((i + 0.5) / 255.0 / 12.92 * 100.0) if (i + 0.5) / 255.0 <= 0.040449936
    else (((i + 0.5) / 255.0 + 0.055) / 1.055) ** 2.4 * 100.0
    for i in range(255)
)
_TWO_PI = math.pi * 2.0


def _sanitize_radians(angle: float) -> float:
    return (angle + math.pi * 8.0) % _TWO_PI


def _chromatic_adaptation(component: float) -> float:
    af = abs(component) ** 0.42
    return _signum(component) * 400.0 * af / (af + 27.13)


def _inverse_chromatic_adaptation(adapted: float) -> float:
    adapted_abs = abs(adapted)
    base = max(0.0, 27.13 * adapted_abs / (400.0 - adapted_abs))
    return _signum(adapted) * base ** (1.0 / 0.42)


def _hue_of(lin: list[float]) -> float:
    m = _SCALED_DISCOUNT_FROM_LINRGB
    r = _chromatic_adaptation(m[0][0] * lin[0] + m[0][1] * lin[1] + m[0][2] * lin[2])
    g = _chromatic_adaptation(m[1][0] * lin[0] + m[1][1] * lin[1] + m[1][2] * lin[2])
    b = _chromatic_adaptation(m[2][0] * lin[0] + m[2][1] * lin[1] + m[2][2] * lin[2])
    a = (11.0 * r + -12.0 * g + b) / 11.0
    bb = (r + g - 2.0 * b) / 9.0
    return math.atan2(bb, a)


def _in_cyclic_order(a: float, b: float, c: float) -> bool:
    return _sanitize_radians(b - a) < _sanitize_radians(c - a)


def _set_coordinate(source: list[float], coordinate: float, target: list[float], axis: int) -> list[float]:
    t = (coordinate - source[axis]) / (target[axis] - source[axis])
    return [source[i] + (target[i] - source[i]) * t for i in range(3)]


def _nth_vertex(y: float, n: int) -> list[float] | None:
    kr, kg, kb = _Y_FROM_LINRGB
    coord_a = 0.0 if n % 4 <= 1 else 100.0
    coord_b = 0.0 if n % 2 == 0 else 100.0
    if n < 4:
        g, b = coord_a, coord_b
        r = (y - g * kg - b * kb) / kr
        return [r, g, b] if 0.0 <= r <= 100.0 else None
    if n < 8:
        b, r = coord_a, coord_b
        g = (y - r * kr - b * kb) / kg
        return [r, g, b] if 0.0 <= g <= 100.0 else None
    r, g = coord_a, coord_b
    b = (y - r * kr - g * kg) / kb
    return [r, g, b] if 0.0 <= b <= 100.0 else None


def _bisect_to_segment(y: float, target_hue: float) -> tuple[list[float], list[float]]:
    left: list[float] = [-1.0, -1.0, -1.0]
    right = left
    left_hue = right_hue = 0.0
    initialized = False
    uncut = True
    for n in range(12):
        mid = _nth_vertex(y, n)
        if mid is None:
            continue
        mid_hue = _hue_of(mid)
        if not initialized:
            left = right = mid
            left_hue = right_hue = mid_hue
            initialized = True
            continue
        if uncut or _in_cyclic_order(left_hue, mid_hue, right_hue):
            uncut = False
            if _in_cyclic_order(left_hue, target_hue, mid_hue):
                right, right_hue = mid, mid_hue
            else:
                left, left_hue = mid, mid_hue
    return left, right


def _bisect_to_limit(y: float, target_hue: float) -> list[float]:
    left, right = _bisect_to_segment(y, target_hue)
    left_hue = _hue_of(left)
    for axis in range(3):
        if left[axis] == right[axis]:
            continue
        if left[axis] < right[axis]:
            l_plane = math.floor(_true_delinearized(left[axis]) - 0.5)
            r_plane = math.ceil(_true_delinearized(right[axis]) - 0.5)
        else:
            l_plane = math.ceil(_true_delinearized(left[axis]) - 0.5)
            r_plane = math.floor(_true_delinearized(right[axis]) - 0.5)
        for _ in range(8):
            if abs(r_plane - l_plane) <= 1:
                break
            m_plane = math.floor((l_plane + r_plane) / 2.0)
            mid = _set_coordinate(left, _CRITICAL_PLANES[m_plane], right, axis)
            mid_hue = _hue_of(mid)
            if _in_cyclic_order(left_hue, target_hue, mid_hue):
                right, r_plane = mid, m_plane
            else:
                left, left_hue, l_plane = mid, mid_hue, m_plane
    return [(left[i] + right[i]) / 2.0 for i in range(3)]


_P1_BASE = (50000.0 / 13.0) * _VC_NC * _VC_NBB


def _find_result_by_j(hue_radians: float, chroma: float, y: float) -> int:
    """Метод Ньютона по J: быстрый путь, когда цвет помещается в sRGB."""
    j = math.sqrt(y) * 11.0
    e_hue = 0.25 * (math.cos(hue_radians + 2.0) + 3.8)
    p1 = e_hue * _P1_BASE
    h_sin = math.sin(hue_radians)
    h_cos = math.cos(hue_radians)
    m = _LINRGB_FROM_SCALED_DISCOUNT
    for iteration in range(5):
        j_normalized = j / 100.0
        alpha = 0.0 if chroma == 0.0 or j == 0.0 else chroma / math.sqrt(j_normalized)
        t = (alpha * _T_INNER_COEFF) ** (1.0 / 0.9)
        ac = _VC_AW * j_normalized ** (1.0 / _VC_C / _VC_Z)
        p2 = ac / _VC_NBB
        gamma = 23.0 * (p2 + 0.305) * t / (23.0 * p1 + 11.0 * t * h_cos + 108.0 * t * h_sin)
        a = gamma * h_cos
        b = gamma * h_sin
        ra = (460.0 * p2 + 451.0 * a + 288.0 * b) / 1403.0
        ga = (460.0 * p2 - 891.0 * a - 261.0 * b) / 1403.0
        ba = (460.0 * p2 - 220.0 * a - 6300.0 * b) / 1403.0
        rs = _inverse_chromatic_adaptation(ra)
        gs = _inverse_chromatic_adaptation(ga)
        bs = _inverse_chromatic_adaptation(ba)
        lin = (
            m[0][0] * rs + m[0][1] * gs + m[0][2] * bs,
            m[1][0] * rs + m[1][1] * gs + m[1][2] * bs,
            m[2][0] * rs + m[2][1] * gs + m[2][2] * bs,
        )
        if lin[0] < 0 or lin[1] < 0 or lin[2] < 0:
            return 0
        fnj = _Y_FROM_LINRGB[0] * lin[0] + _Y_FROM_LINRGB[1] * lin[1] + _Y_FROM_LINRGB[2] * lin[2]
        if fnj <= 0:
            return 0
        if iteration == 4 or abs(fnj - y) < 0.002:
            if lin[0] > 100.01 or lin[1] > 100.01 or lin[2] > 100.01:
                return 0
            return _argb_from_linrgb(lin)
        j = j - (fnj - y) * j / (2.0 * fnj)
    return 0


def _solve_to_argb(hue: float, chroma: float, lstar: float) -> int:
    """Цвет sRGB, ближайший к заданным HCT (насыщенность урезается до гаммы)."""
    if chroma < 0.0001 or lstar < 0.0001 or lstar > 99.9999:
        return _argb_from_lstar(lstar)
    hue_radians = math.radians(_sanitize_degrees(hue))
    y = _y_from_lstar(lstar)
    exact = _find_result_by_j(hue_radians, chroma, y)
    if exact != 0:
        return exact
    return _argb_from_linrgb(_bisect_to_limit(y, hue_radians))


class Hct:
    """Цвет в координатах HCT. Значения пересчитаны из итогового sRGB.

    Как и в оригинале, запрошенные тон и насыщенность — лишь цель: после
    попадания в 8-битный sRGB они немного другие, и дальше считается уже
    с фактическими.
    """

    __slots__ = ("argb", "hue", "chroma", "tone")

    def __init__(self, argb: int) -> None:
        self.argb = argb
        self.hue, self.chroma = _cam16_hue_chroma(argb)
        self.tone = _lstar_from_argb(argb)

    @classmethod
    def from_hct(cls, hue: float, chroma: float, tone: float) -> Hct:
        return cls(_solve_to_argb(hue, chroma, tone))

    def __repr__(self) -> str:
        return f"Hct({_hex(self.argb)}, h={self.hue:.2f}, c={self.chroma:.2f}, t={self.tone:.2f})"


# ---------------------------------------------------------------------------
# Палитры и контраст
# ---------------------------------------------------------------------------

class TonalPalette:
    """Все тона одного оттенка и одной насыщенности."""

    __slots__ = ("hue", "chroma", "_cache")

    def __init__(self, hue: float, chroma: float) -> None:
        self.hue = hue
        self.chroma = chroma
        self._cache: dict[float, int] = {}

    def tone(self, tone: float) -> int:
        argb = self._cache.get(tone)
        if argb is None:
            argb = self._cache[tone] = _solve_to_argb(self.hue, self.chroma, tone)
        return argb


def _ratio_of_ys(y1: float, y2: float) -> float:
    lighter, darker = (y1, y2) if y1 > y2 else (y2, y1)
    return (lighter + 5.0) / (darker + 5.0)


def _ratio_of_tones(t1: float, t2: float) -> float:
    return _ratio_of_ys(_y_from_lstar(_clamp(0.0, 100.0, t1)), _y_from_lstar(_clamp(0.0, 100.0, t2)))


def _lighter(tone: float, ratio: float) -> float:
    if tone < 0.0 or tone > 100.0:
        return -1.0
    dark_y = _y_from_lstar(tone)
    light_y = ratio * (dark_y + 5.0) - 5.0
    real = _ratio_of_ys(light_y, dark_y)
    if real < ratio and abs(real - ratio) > 0.04:
        return -1.0
    value = _lstar_from_y(light_y) + 0.4
    return -1.0 if value < 0 or value > 100 else value


def _darker(tone: float, ratio: float) -> float:
    if tone < 0.0 or tone > 100.0:
        return -1.0
    light_y = _y_from_lstar(tone)
    dark_y = (light_y + 5.0) / ratio - 5.0
    real = _ratio_of_ys(light_y, dark_y)
    if real < ratio and abs(real - ratio) > 0.04:
        return -1.0
    value = _lstar_from_y(dark_y) - 0.4
    return -1.0 if value < 0 or value > 100 else value


def _lighter_unsafe(tone: float, ratio: float) -> float:
    value = _lighter(tone, ratio)
    return 100.0 if value < 0 else value


def _darker_unsafe(tone: float, ratio: float) -> float:
    value = _darker(tone, ratio)
    return 0.0 if value < 0 else value


def _prefers_light_foreground(tone: float) -> bool:
    return _round(tone) < 60


def _foreground_tone(bg_tone: float, ratio: float) -> float:
    """Тон текста, который на фоне bg_tone даёт контраст не ниже ratio."""
    lighter = _lighter_unsafe(bg_tone, ratio)
    darker = _darker_unsafe(bg_tone, ratio)
    lighter_ratio = _ratio_of_tones(lighter, bg_tone)
    darker_ratio = _ratio_of_tones(darker, bg_tone)
    if _prefers_light_foreground(bg_tone):
        negligible = (abs(lighter_ratio - darker_ratio) < 0.1
                      and lighter_ratio < ratio and darker_ratio < ratio)
        if lighter_ratio >= ratio or lighter_ratio >= darker_ratio or negligible:
            return lighter
        return darker
    if darker_ratio >= ratio or darker_ratio >= lighter_ratio:
        return darker
    return lighter


def _curve(low: float, normal: float, medium: float, high: float) -> Callable[[float], float]:
    """Требование к контрасту в зависимости от уровня контраста схемы."""

    def get(level: float) -> float:
        if level <= -1.0:
            return low
        if level < 0.0:
            return low + (normal - low) * (level + 1.0)
        if level < 0.5:
            return normal + (medium - normal) * (level / 0.5)
        if level < 1.0:
            return medium + (high - medium) * ((level - 0.5) / 0.5)
        return high

    return get


# ---------------------------------------------------------------------------
# Температура цвета — нужна вариантам content и fidelity
# ---------------------------------------------------------------------------

def _raw_temperature(argb: int) -> float:
    _, a, b = _lab_from_argb(argb)
    hue = _sanitize_degrees(math.degrees(math.atan2(b, a)))
    chroma = math.hypot(a, b)
    return -0.5 + 0.02 * chroma ** 1.07 * math.cos(math.radians(_sanitize_degrees(hue - 50.0)))


class _Temperature:
    """Тёплые и холодные родственники цвета (TemperatureCache в оригинале)."""

    def __init__(self, source: Hct) -> None:
        self.source = source
        self.by_hue = [Hct.from_hct(float(h), source.chroma, source.tone) for h in range(361)]
        self.temp = [_raw_temperature(h.argb) for h in self.by_hue]
        source_temp = _raw_temperature(source.argb)
        # Сортировка устойчивая, как Collections.sort: при равной
        # температуре порядок решает исходный список, а он важен для
        # выбора крайних оттенков.
        entries = [(t, h.hue) for t, h in zip(self.temp, self.by_hue)] + [(source_temp, source.hue)]
        ordered = sorted(range(len(entries)), key=lambda i: entries[i][0])
        self.coldest_temp, self.coldest_hue = entries[ordered[0]]
        self.warmest_temp, self.warmest_hue = entries[ordered[-1]]
        self.source_temp = source_temp

    def relative(self, temp: float) -> float:
        span = self.warmest_temp - self.coldest_temp
        if span == 0:
            return 0.5
        return (temp - self.coldest_temp) / span

    @staticmethod
    def _between(angle: float, a: float, b: float) -> bool:
        if a < b:
            return a <= angle <= b
        return a <= angle or angle <= b

    def complement(self) -> Hct:
        coldest_hue, warmest_hue = self.coldest_hue, self.warmest_hue
        span = self.warmest_temp - self.coldest_temp
        cold_to_warm = self._between(self.source.hue, coldest_hue, warmest_hue)
        start = warmest_hue if cold_to_warm else coldest_hue
        end = coldest_hue if cold_to_warm else warmest_hue
        smallest_error = 1000.0
        answer = self.by_hue[_round(self.source.hue)]
        if span == 0:
            # Серый цвет: температура у всех оттенков одна. В Java здесь
            # деление на ноль даёт NaN, ни одна ошибка не «меньше», и
            # ответом остаётся исходный оттенок — повторяем это явно.
            return answer
        target = 1.0 - self.relative(self.source_temp)
        addend = 0.0
        while addend <= 360.0:
            hue = _sanitize_degrees(start + addend)
            addend += 1.0
            if not self._between(hue, start, end):
                continue
            index = _round(hue)
            relative = (self.temp[index] - self.coldest_temp) / span
            error = abs(target - relative)
            if error < smallest_error:
                smallest_error = error
                answer = self.by_hue[index]
        return answer

    def analogous(self, count: int = 5, divisions: int = 12) -> list[Hct]:
        start_hue = _round(self.source.hue)
        start = self.by_hue[start_hue]
        last = self.relative(self.temp[start_hue])
        colors = [start]
        total_abs = 0.0
        for i in range(360):
            hue = (start_hue + i) % 360
            temp = self.relative(self.temp[hue])
            total_abs += abs(temp - last)
            last = temp
        addend = 1
        step = total_abs / divisions
        total = 0.0
        last = self.relative(self.temp[start_hue])
        while len(colors) < divisions:
            hue = (start_hue + addend) % 360
            hct = self.by_hue[hue]
            temp = self.relative(self.temp[hue])
            total += abs(temp - last)
            desired = len(colors) * step
            satisfied = total >= desired
            index_addend = 1
            while satisfied and len(colors) < divisions:
                colors.append(hct)
                desired = (len(colors) + index_addend) * step
                satisfied = total >= desired
                index_addend += 1
            last = temp
            addend += 1
            if addend > 360:
                while len(colors) < divisions:
                    colors.append(hct)
                break
        answers = [self.source]
        ccw = math.floor((count - 1) / 2.0)
        for i in range(1, ccw + 1):
            answers.insert(0, colors[(-i) % len(colors)])
        for i in range(1, count - ccw):
            answers.append(colors[i % len(colors)])
        return answers


def _fix_if_disliked(hct: Hct) -> Hct:
    """Болотно-жёлтые тёмные тона люди стабильно не любят — осветляем."""
    disliked = (90 <= _round(hct.hue) <= 111 and _round(hct.chroma) > 16
                and _round(hct.tone) < 65)
    return Hct.from_hct(hct.hue, hct.chroma, 70.0) if disliked else hct


# ---------------------------------------------------------------------------
# Схема: варианты и роли
# ---------------------------------------------------------------------------

VARIANTS: tuple[str, ...] = (
    "tonal_spot", "vibrant", "expressive", "content", "fidelity",
    "neutral", "monochrome", "rainbow", "fruit_salad",
)

# Границы участков круга оттенков, на которых vibrant и expressive
# поворачивают вторичный и третичный цвет на разный угол. Сетки у двух
# вариантов разные — так в оригинале.
_VIBRANT_HUES = (0.0, 41.0, 61.0, 101.0, 131.0, 181.0, 251.0, 301.0, 360.0)
_EXPRESSIVE_HUES = (0.0, 21.0, 51.0, 121.0, 151.0, 191.0, 271.0, 321.0, 360.0)


def _rotated_hue(source_hue: float, hues: tuple[float, ...], rotations: tuple[float, ...]) -> float:
    for i in range(len(hues) - 1):
        if hues[i] < source_hue < hues[i + 1]:
            return _sanitize_degrees(source_hue + rotations[i])
    return source_hue


class _Scheme:
    """Палитры варианта и уже посчитанные тона ролей."""

    def __init__(self, source: Hct, variant: str, dark: bool, contrast: float) -> None:
        self.source = source
        self.variant = variant
        self.dark = dark
        self.contrast = contrast
        self.fidelity = variant in ("content", "fidelity")
        self.monochrome = variant == "monochrome"
        self.tones: dict[str, float] = {}
        h, c = source.hue, source.chroma
        P = TonalPalette
        if variant == "tonal_spot":
            pal = (P(h, 36.0), P(h, 16.0), P(_sanitize_degrees(h + 60.0), 24.0), P(h, 6.0), P(h, 8.0))
        elif variant == "vibrant":
            pal = (P(h, 200.0),
                   P(_rotated_hue(h, _VIBRANT_HUES, (18, 15, 10, 12, 15, 18, 15, 12, 12)), 24.0),
                   P(_rotated_hue(h, _VIBRANT_HUES, (35, 30, 20, 25, 30, 35, 30, 25, 25)), 32.0),
                   P(h, 10.0), P(h, 10.0))
        elif variant == "expressive":
            pal = (P(_sanitize_degrees(h + 240.0), 40.0),
                   P(_rotated_hue(h, _EXPRESSIVE_HUES, (45, 95, 45, 20, 45, 90, 45, 45, 45)), 24.0),
                   P(_rotated_hue(h, _EXPRESSIVE_HUES, (120, 120, 20, 45, 20, 15, 20, 120, 120)), 32.0),
                   P(_sanitize_degrees(h + 15.0), 8.0), P(_sanitize_degrees(h + 15.0), 12.0))
        elif variant in ("content", "fidelity"):
            temperature = _temperature(source.argb)
            if variant == "content":
                third = _fix_if_disliked(temperature.analogous(3, 6)[2])
            else:
                third = _fix_if_disliked(temperature.complement())
            pal = (P(h, c), P(h, max(c - 32.0, c * 0.5)), P(third.hue, third.chroma),
                   P(h, c / 8.0), P(h, c / 8.0 + 4.0))
        elif variant == "neutral":
            pal = (P(h, 12.0), P(h, 8.0), P(h, 16.0), P(h, 2.0), P(h, 2.0))
        elif variant == "monochrome":
            pal = (P(h, 0.0), P(h, 0.0), P(h, 0.0), P(h, 0.0), P(h, 0.0))
        elif variant == "rainbow":
            pal = (P(h, 48.0), P(h, 16.0), P(_sanitize_degrees(h + 60.0), 24.0), P(h, 0.0), P(h, 0.0))
        elif variant == "fruit_salad":
            pal = (P(_sanitize_degrees(h - 50.0), 48.0), P(_sanitize_degrees(h - 50.0), 36.0),
                   P(h, 36.0), P(h, 10.0), P(h, 16.0))
        else:
            raise ValueError(f"неизвестный вариант схемы: {variant}")
        self.palettes: dict[str, TonalPalette] = {
            "primary": pal[0], "secondary": pal[1], "tertiary": pal[2],
            "neutral": pal[3], "neutral_variant": pal[4], "error": P(25.0, 84.0),
        }


@lru_cache(maxsize=32)
def _temperature(argb: int) -> _Temperature:
    # 361 решение HCT — самое дорогое место во всём модуле, а для одного
    # исходного цвета ответ всегда один и тот же.
    return _Temperature(Hct(argb))


class _Role:
    """Динамический цвет (DynamicColor): палитра, желаемый тон и правила."""

    __slots__ = ("name", "palette", "tone", "background", "second_background",
                 "curve", "pair", "is_background")

    def __init__(self, name: str, palette: str, tone: Callable[[_Scheme], float],
                 background: str | Callable[[_Scheme], str] | None = None,
                 curve: Callable[[float], float] | None = None,
                 pair: tuple[str, str, float, str, bool] | None = None,
                 is_background: bool = False, second_background: str | None = None) -> None:
        self.name = name
        self.palette = palette
        self.tone = tone
        self.background = background
        self.second_background = second_background
        self.curve = curve
        self.pair = pair
        self.is_background = is_background


def _highest_surface(s: _Scheme) -> str:
    return "surface_bright" if s.dark else "surface_dim"


def _get_tone(role: _Role, s: _Scheme) -> float:
    cached = s.tones.get(role.name)
    if cached is not None:
        return cached
    tone = _compute_tone(role, s)
    s.tones[role.name] = tone
    return tone


def _background_of(role: _Role, s: _Scheme) -> _Role | None:
    bg = role.background
    if bg is None:
        return None
    return _ROLES[bg(s) if callable(bg) else bg]


def _compute_tone(role: _Role, s: _Scheme) -> float:
    decreasing = s.contrast < 0
    if role.pair is not None:
        a_name, b_name, delta, polarity, stay_together = role.pair
        role_a, role_b = _ROLES[a_name], _ROLES[b_name]
        bg = _background_of(role, s)
        bg_tone = _get_tone(bg, s) if bg is not None else 0.0
        a_nearer = (polarity == "nearer" or (polarity == "lighter" and not s.dark)
                    or (polarity == "darker" and s.dark))
        nearer, farther = (role_a, role_b) if a_nearer else (role_b, role_a)
        am_nearer = role.name == nearer.name
        direction = 1.0 if s.dark else -1.0
        n_contrast = nearer.curve(s.contrast)  # type: ignore[misc]
        f_contrast = farther.curve(s.contrast)  # type: ignore[misc]
        n_initial = nearer.tone(s)
        n_tone = (n_initial if _ratio_of_tones(bg_tone, n_initial) >= n_contrast
                  else _foreground_tone(bg_tone, n_contrast))
        f_initial = farther.tone(s)
        f_tone = (f_initial if _ratio_of_tones(bg_tone, f_initial) >= f_contrast
                  else _foreground_tone(bg_tone, f_contrast))
        if decreasing:
            n_tone = _foreground_tone(bg_tone, n_contrast)
            f_tone = _foreground_tone(bg_tone, f_contrast)
        if (f_tone - n_tone) * direction < delta:
            f_tone = _clamp(0.0, 100.0, n_tone + delta * direction)
            if (f_tone - n_tone) * direction < delta:
                n_tone = _clamp(0.0, 100.0, f_tone - delta * direction)
        # Тона 50–59 — «неудобная зона»: на них ни белый, ни чёрный текст
        # не даёт хорошего контраста, поэтому пары оттуда уводятся.
        if 50 <= n_tone < 60:
            if direction > 0:
                n_tone = 60.0
                f_tone = max(f_tone, n_tone + delta * direction)
            else:
                n_tone = 49.0
                f_tone = min(f_tone, n_tone + delta * direction)
        elif 50 <= f_tone < 60:
            if stay_together:
                if direction > 0:
                    n_tone = 60.0
                    f_tone = max(f_tone, n_tone + delta * direction)
                else:
                    n_tone = 49.0
                    f_tone = min(f_tone, n_tone + delta * direction)
            elif direction > 0:
                f_tone = 60.0
            else:
                f_tone = 49.0
        return n_tone if am_nearer else f_tone

    answer = role.tone(s)
    bg = _background_of(role, s)
    if bg is None:
        return answer
    bg_tone = _get_tone(bg, s)
    desired = role.curve(s.contrast)  # type: ignore[misc]
    if _ratio_of_tones(bg_tone, answer) < desired:
        answer = _foreground_tone(bg_tone, desired)
    if decreasing:
        answer = _foreground_tone(bg_tone, desired)
    if role.is_background and 50 <= answer < 60:
        answer = 49.0 if _ratio_of_tones(49.0, bg_tone) >= desired else 60.0
    if role.second_background is not None:
        bg1 = bg_tone
        bg2 = _get_tone(_ROLES[role.second_background], s)
        upper, lower = max(bg1, bg2), min(bg1, bg2)
        if _ratio_of_tones(upper, answer) >= desired and _ratio_of_tones(lower, answer) >= desired:
            return answer
        light_option = _lighter(upper, desired)
        dark_option = _darker(lower, desired)
        available = [t for t in (light_option, dark_option) if t != -1]
        if _prefers_light_foreground(bg1) or _prefers_light_foreground(bg2):
            return 100.0 if light_option < 0 else light_option
        if len(available) == 1:
            return available[0]
        return 0.0 if dark_option < 0 else dark_option
    return answer


def _desired_chroma_tone(hue: float, chroma: float, tone: float, by_decreasing_tone: bool) -> float:
    """Тон, на котором палитра ближе всего к нужной насыщенности."""
    answer = tone
    closest = Hct.from_hct(hue, chroma, tone)
    if closest.chroma < chroma:
        peak = closest.chroma
        while closest.chroma < chroma:
            answer += -1.0 if by_decreasing_tone else 1.0
            candidate = Hct.from_hct(hue, chroma, answer)
            if peak > candidate.chroma:
                break
            if abs(candidate.chroma - chroma) < 0.4:
                break
            if abs(candidate.chroma - chroma) < abs(closest.chroma - chroma):
                closest = candidate
            peak = max(peak, candidate.chroma)
    return answer


def _secondary_container_tone(s: _Scheme) -> float:
    # Монохромную схему оригинал здесь осветляет до 85, эталон — нет;
    # держимся эталона.
    initial = 30.0 if s.dark else 90.0
    if not s.fidelity:
        return initial
    pal = s.palettes["secondary"]
    return _desired_chroma_tone(pal.hue, pal.chroma, initial, not s.dark)


def _tertiary_container_tone(s: _Scheme) -> float:
    if s.monochrome:
        return 60.0 if s.dark else 49.0
    if not s.fidelity:
        return 30.0 if s.dark else 90.0
    pal = s.palettes["tertiary"]
    proposed = Hct(pal.tone(s.source.tone))
    return _fix_if_disliked(proposed).tone


def _by_mode(dark: float, light: float, mono: tuple[float, float] | None = None) -> Callable[[_Scheme], float]:
    """Тон, зависящий только от тёмной/светлой темы (и, может, монохрома)."""

    def tone(s: _Scheme) -> float:
        if mono is not None and s.monochrome:
            return mono[0] if s.dark else mono[1]
        return dark if s.dark else light

    return tone


def _fixed(normal: float, mono: float) -> Callable[[_Scheme], float]:
    return lambda s: mono if s.monochrome else normal


_C_TEXT = _curve(4.5, 7.0, 11.0, 21.0)
_C_ACCENT = _curve(3.0, 4.5, 7.0, 7.0)
_C_CONTAINER = _curve(1.0, 1.0, 3.0, 4.5)
_C_VARIANT_TEXT = _curve(3.0, 4.5, 7.0, 11.0)


def _surface(dark: Callable[[float], float] | float, light: Callable[[float], float] | float) -> Callable[[_Scheme], float]:
    def tone(s: _Scheme) -> float:
        value = dark if s.dark else light
        return value(s.contrast) if callable(value) else value

    return tone


def _build_roles() -> dict[str, _Role]:
    R = _Role
    hs = _highest_surface
    roles = [
        R("background", "neutral", _by_mode(6, 98), is_background=True),
        R("on_background", "neutral", _by_mode(90, 10), "background", _curve(3, 3, 4.5, 7)),
        R("surface", "neutral", _by_mode(6, 98), is_background=True),
        R("surface_dim", "neutral", _surface(6, _curve(87, 87, 80, 75)), is_background=True),
        R("surface_bright", "neutral", _surface(_curve(24, 24, 29, 34), 98), is_background=True),
        R("surface_container_lowest", "neutral", _surface(_curve(4, 4, 2, 0), 100), is_background=True),
        R("surface_container_low", "neutral",
          _surface(_curve(10, 10, 11, 12), _curve(96, 96, 96, 95)), is_background=True),
        R("surface_container", "neutral",
          _surface(_curve(12, 12, 16, 20), _curve(94, 94, 92, 90)), is_background=True),
        R("surface_container_high", "neutral",
          _surface(_curve(17, 17, 21, 25), _curve(92, 92, 88, 85)), is_background=True),
        R("surface_container_highest", "neutral",
          _surface(_curve(22, 22, 26, 30), _curve(90, 90, 84, 80)), is_background=True),
        R("on_surface", "neutral", _by_mode(90, 10), hs, _C_TEXT),
        R("surface_variant", "neutral_variant", _by_mode(30, 90), is_background=True),
        R("on_surface_variant", "neutral_variant", _by_mode(80, 30), hs, _C_VARIANT_TEXT),
        R("inverse_surface", "neutral", _by_mode(90, 20)),
        R("inverse_on_surface", "neutral", _by_mode(20, 95), "inverse_surface", _C_TEXT),
        R("outline", "neutral_variant", _by_mode(60, 50), hs, _curve(1.5, 3, 4.5, 7)),
        R("outline_variant", "neutral_variant", _by_mode(30, 80), hs, _C_CONTAINER),
        R("shadow", "neutral", lambda s: 0.0),
        R("scrim", "neutral", lambda s: 0.0),
        R("surface_tint", "primary", _by_mode(80, 40), is_background=True),

        R("primary", "primary", _by_mode(80, 40, (100, 0)), hs, _C_ACCENT,
          ("primary_container", "primary", 10, "nearer", False), True),
        # Нижняя точка кривой у on_primary в эталоне — 3, а не 4.5, как у
        # прочих «on_»: при уменьшенном контрасте текст на primary
        # приглушается сильнее. Видно только при contrast < 0.
        R("on_primary", "primary", _by_mode(20, 100, (10, 90)), "primary", _curve(3, 7, 11, 21)),
        R("primary_container", "primary",
          lambda s: s.source.tone if s.fidelity else _by_mode(30, 90, (85, 25))(s), hs, _C_CONTAINER,
          ("primary_container", "primary", 10, "nearer", False), True),
        # У content/fidelity текст на контейнере считается от уже
        # исправленного тона контейнера, а не от желаемого — так в эталоне,
        # и на чисто чёрном исходном цвете это меняет серый текст на белый.
        R("on_primary_container", "primary",
          lambda s: (_foreground_tone(_get_tone(_ROLES["primary_container"], s), 4.5) if s.fidelity
                     else _by_mode(90, 10, (0, 100))(s)),
          "primary_container", _C_TEXT),
        R("inverse_primary", "primary", _by_mode(40, 80), "inverse_surface", _C_ACCENT),

        R("secondary", "secondary", _by_mode(80, 40), hs, _C_ACCENT,
          ("secondary_container", "secondary", 10, "nearer", False), True),
        R("on_secondary", "secondary", _by_mode(20, 100, (10, 100)), "secondary", _C_TEXT),
        R("secondary_container", "secondary", _secondary_container_tone, hs, _C_CONTAINER,
          ("secondary_container", "secondary", 10, "nearer", False), True),
        R("on_secondary_container", "secondary",
          lambda s: (_foreground_tone(_get_tone(_ROLES["secondary_container"], s), 4.5)
                     if s.fidelity and not s.monochrome else _by_mode(90, 10)(s)),
          "secondary_container", _C_TEXT),

        R("tertiary", "tertiary", _by_mode(80, 40, (90, 25)), hs, _C_ACCENT,
          ("tertiary_container", "tertiary", 10, "nearer", False), True),
        R("on_tertiary", "tertiary", _by_mode(20, 100, (10, 90)), "tertiary", _C_TEXT),
        R("tertiary_container", "tertiary", _tertiary_container_tone, hs, _C_CONTAINER,
          ("tertiary_container", "tertiary", 10, "nearer", False), True),
        R("on_tertiary_container", "tertiary",
          lambda s: (_foreground_tone(_get_tone(_ROLES["tertiary_container"], s), 4.5)
                     if s.fidelity and not s.monochrome else _by_mode(90, 10, (0, 100))(s)),
          "tertiary_container", _C_TEXT),

        R("error", "error", _by_mode(80, 40), hs, _C_ACCENT,
          ("error_container", "error", 10, "nearer", False), True),
        R("on_error", "error", _by_mode(20, 100), "error", _C_TEXT),
        R("error_container", "error", _by_mode(30, 90), hs, _C_CONTAINER,
          ("error_container", "error", 10, "nearer", False), True),
        R("on_error_container", "error", _by_mode(90, 10), "error_container", _C_TEXT),
    ]
    for name in ("primary", "secondary", "tertiary"):
        mono = {"primary": (40, 30, 100, 90), "secondary": (80, 70, 10, 25),
                "tertiary": (40, 30, 100, 90)}[name]
        pair = (f"{name}_fixed", f"{name}_fixed_dim", 10, "lighter", True)
        roles += [
            R(f"{name}_fixed", name, _fixed(90, mono[0]), hs, _C_CONTAINER, pair, True),
            R(f"{name}_fixed_dim", name, _fixed(80, mono[1]), hs, _C_CONTAINER, pair, True),
            R(f"on_{name}_fixed", name, _fixed(10, mono[2]), f"{name}_fixed_dim", _C_TEXT,
              second_background=f"{name}_fixed"),
            R(f"on_{name}_fixed_variant", name, _fixed(30, mono[3]), f"{name}_fixed_dim",
              _C_VARIANT_TEXT, second_background=f"{name}_fixed"),
        ]
    return {role.name: role for role in roles}


_ROLES = _build_roles()

# Имена ролей — как у matugen, чтобы прототипы и приложение говорили на
# одном языке. source_color — сам исходный цвет, без пересчёта.
ROLES: tuple[str, ...] = tuple(sorted([*_ROLES, "source_color"]))


# ---------------------------------------------------------------------------
# Общедоступное
# ---------------------------------------------------------------------------

def _parse(color: str) -> int:
    text = color.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"ожидался цвет вида #rrggbb: {color!r}")
    return 0xFF000000 | int(text, 16)


def _hex(argb: int) -> str:
    return "#%06x" % (argb & 0xFFFFFF)


def _check_variant(variant: str) -> str:
    variant = variant.replace("-", "_").removeprefix("scheme_")
    if variant not in VARIANTS:
        raise ValueError(f"неизвестный вариант схемы: {variant}")
    return variant


@lru_cache(maxsize=64)
def _scheme_cached(argb: int, dark: bool, variant: str, contrast: float) -> tuple[tuple[str, str], ...]:
    s = _Scheme(Hct(argb), variant, dark, contrast)
    out = [("source_color", _hex(argb))]
    for name, role in _ROLES.items():
        out.append((name, _hex(s.palettes[role.palette].tone(_get_tone(role, s)))))
    return tuple(sorted(out))


def scheme(seed: str, dark: bool, variant: str = "tonal_spot",
           contrast: float = 0.0) -> dict[str, str]:
    """Роли Material You для исходного цвета: {"primary": "#rrggbb", …}.

    ``contrast`` — от −1 (минимальный) через 0 (как в спецификации) до 1
    (максимальный). Результат кешируется: одно и то же приложение
    перерисовывается часто, а цвет его значка не меняется.
    """
    return dict(_scheme_cached(_parse(seed), bool(dark), _check_variant(variant),
                               float(_clamp(-1.0, 1.0, contrast))))


@lru_cache(maxsize=16)
def _palettes(argb: int, variant: str) -> dict[str, TonalPalette]:
    # Палитры от тёмной/светлой темы не зависят, поэтому схема — любая.
    return _Scheme(Hct(argb), variant, True, 0.0).palettes


def tonal(seed: str, palette: str, tone: float, variant: str = "tonal_spot") -> str:
    """Отдельный тон палитры (primary/secondary/tertiary/neutral/
    neutral_variant/error) — для своих оттенков сверх ролей."""
    palettes = _palettes(_parse(seed), _check_variant(variant))
    if palette not in palettes:
        raise ValueError(f"неизвестная палитра: {palette}")
    return _hex(palettes[palette].tone(float(_clamp(0.0, 100.0, tone))))


def css_variables(roles: dict[str, str], prefix: str = "--md-") -> str:
    """Строки объявлений CSS: «--md-primary-container: #…;» (без селектора)."""
    return "\n".join(f"{prefix}{name.replace('_', '-')}: {value};" for name, value in roles.items())


# ---------------------------------------------------------------------------
# Исходный цвет из картинки: квантование Ву и оценка как в оригинале
# ---------------------------------------------------------------------------

_BITS = 5
_SIDE = (1 << _BITS) + 1  # 33: нулевой слой — поле для префиксных сумм
_SIDE2 = _SIDE * _SIDE


def _prefix_3d(values: list[float]) -> list[float]:
    """Трёхмерные префиксные суммы по кубу 33×33×33.

    Тройной цикл из оригинала в Python обходится в десятки миллисекунд;
    accumulate и zip делают то же самое внутри C.
    """
    planes = []
    for r in range(_SIDE):
        rows = []
        prev = [0.0] * _SIDE
        for g in range(_SIDE):
            base = r * _SIDE2 + g * _SIDE
            row = list(accumulate(values[base:base + _SIDE]))
            prev = [a + b for a, b in zip(row, prev)]
            rows.append(prev)
        planes.append(rows)
    out: list[float] = []
    prev_plane = [[0.0] * _SIDE for _ in range(_SIDE)]
    for r in range(_SIDE):
        plane = [[a + b for a, b in zip(row, prev_row)] for row, prev_row in zip(planes[r], prev_plane)]
        for row in plane:
            out.extend(row)
        prev_plane = plane
    return out


class _Wu:
    """Квантователь Ву: режет куб RGB на коробки с наибольшей дисперсией."""

    def __init__(self, counts: dict[tuple[int, int, int], int]) -> None:
        size = _SIDE ** 3
        w = [0.0] * size
        mr = [0.0] * size
        mg = [0.0] * size
        mb = [0.0] * size
        m2 = [0.0] * size
        shift = 8 - _BITS
        for (r, g, b), n in counts.items():
            i = ((r >> shift) + 1) * _SIDE2 + ((g >> shift) + 1) * _SIDE + (b >> shift) + 1
            w[i] += n
            mr[i] += r * n
            mg[i] += g * n
            mb[i] += b * n
            m2[i] += n * (r * r + g * g + b * b)
        self.moments = [_prefix_3d(m) for m in (w, mr, mg, mb)]
        self.m2 = _prefix_3d(m2)

    @staticmethod
    def _volume(box: list[int], m: list[float]) -> float:
        r0, r1, g0, g1, b0, b1 = box
        r0 *= _SIDE2
        r1 *= _SIDE2
        g0 *= _SIDE
        g1 *= _SIDE
        return (m[r1 + g1 + b1] - m[r1 + g1 + b0] - m[r1 + g0 + b1] + m[r1 + g0 + b0]
                - m[r0 + g1 + b1] + m[r0 + g1 + b0] + m[r0 + g0 + b1] - m[r0 + g0 + b0])

    def _variance(self, box: list[int]) -> float:
        w, mr, mg, mb = (self._volume(box, m) for m in self.moments)
        xx = self._volume(box, self.m2)
        return xx - (mr * mr + mg * mg + mb * mb) / w

    def _maximize(self, box: list[int], axis: int, whole: list[float]) -> tuple[int, float]:
        r0, r1, g0, g1, b0, b1 = box
        # Сумма по «срезу» коробки в плоскости axis = p: четыре угла.
        if axis == 0:
            stride, corners = _SIDE2, (g1 * _SIDE + b1, g1 * _SIDE + b0, g0 * _SIDE + b1, g0 * _SIDE + b0)
            first, last = r0, r1
        elif axis == 1:
            stride, corners = _SIDE, (r1 * _SIDE2 + b1, r1 * _SIDE2 + b0, r0 * _SIDE2 + b1, r0 * _SIDE2 + b0)
            first, last = g0, g1
        else:
            stride, corners = 1, (r1 * _SIDE2 + g1 * _SIDE, r1 * _SIDE2 + g0 * _SIDE,
                                  r0 * _SIDE2 + g1 * _SIDE, r0 * _SIDE2 + g0 * _SIDE)
            first, last = b0, b1
        c11, c10, c01, c00 = corners

        def face(m: list[float], p: int) -> float:
            o = p * stride
            return m[o + c11] - m[o + c10] - m[o + c01] + m[o + c00]

        moments = self.moments
        bottom = [face(m, first) for m in moments]
        ww, wr, wg, wb = whole
        best, cut = 0.0, -1
        for i in range(first + 1, last):
            hw = face(moments[0], i) - bottom[0]
            if hw == 0:
                continue
            hr = face(moments[1], i) - bottom[1]
            hg = face(moments[2], i) - bottom[2]
            hb = face(moments[3], i) - bottom[3]
            temp = (hr * hr + hg * hg + hb * hb) / hw
            ow = ww - hw
            if ow == 0:
                continue
            orr, og, ob = wr - hr, wg - hg, wb - hb
            temp += (orr * orr + og * og + ob * ob) / ow
            if temp > best:
                best, cut = temp, i
        return cut, best

    def _cut(self, one: list[int], two: list[int]) -> bool:
        whole = [self._volume(one, m) for m in self.moments]
        cut_r, max_r = self._maximize(one, 0, whole)
        cut_g, max_g = self._maximize(one, 1, whole)
        cut_b, max_b = self._maximize(one, 2, whole)
        if max_r >= max_g and max_r >= max_b:
            if cut_r < 0:
                return False
            axis, cut = 0, cut_r
        elif max_g >= max_r and max_g >= max_b:
            axis, cut = 1, cut_g
        else:
            axis, cut = 2, cut_b
        two[:] = one
        one[axis * 2 + 1] = cut
        two[axis * 2] = cut
        return True

    @staticmethod
    def _vol(box: list[int]) -> int:
        return (box[1] - box[0]) * (box[3] - box[2]) * (box[5] - box[4])

    def quantize(self, max_colors: int) -> dict[int, int]:
        boxes = [[0, _SIDE - 1, 0, _SIDE - 1, 0, _SIDE - 1]] + [[0] * 6 for _ in range(max_colors - 1)]
        variance = [0.0] * max_colors
        count = max_colors
        nxt = 0
        i = 1
        while i < max_colors:
            if self._cut(boxes[nxt], boxes[i]):
                variance[nxt] = self._variance(boxes[nxt]) if self._vol(boxes[nxt]) > 1 else 0.0
                variance[i] = self._variance(boxes[i]) if self._vol(boxes[i]) > 1 else 0.0
            else:
                variance[nxt] = 0.0
                i -= 1
            nxt = 0
            temp = variance[0]
            for j in range(1, i + 1):
                if variance[j] > temp:
                    temp, nxt = variance[j], j
            if temp <= 0.0:
                count = i + 1
                break
            i += 1
        result: dict[int, int] = {}
        for box in boxes[:count]:
            w, mr, mg, mb = (self._volume(box, m) for m in self.moments)
            if w > 0:
                argb = _argb(_round(mr / w), _round(mg / w), _round(mb / w))
                result[argb] = result.get(argb, 0) + int(w)
        return result


def _score(population: dict[int, int], fallback: int) -> int:
    """Лучший исходный цвет: насыщенный и занимающий заметную долю картинки."""
    total = sum(population.values())
    if total <= 0:
        return fallback
    colors = [(Hct(argb), n) for argb, n in population.items()]
    hue_population = [0] * 360
    for hct, n in colors:
        hue_population[int(hct.hue) % 360] += n
    excited = [0.0] * 360
    for hue, n in enumerate(hue_population):
        if n:
            proportion = n / total
            for i in range(hue - 14, hue + 16):
                excited[i % 360] += proportion
    scored = []
    for hct, _ in colors:
        proportion = excited[_round(hct.hue) % 360]
        # Почти серые и редкие цвета отбрасываются: из них не выйдет
        # палитры, которую узнаёшь как «цвет этого приложения».
        if hct.chroma < 5.0 or proportion <= 0.01:
            continue
        weight = 0.1 if hct.chroma < 48.0 else 0.3
        scored.append((proportion * 100.0 * 0.7 + (hct.chroma - 48.0) * weight, hct))
    scored.sort(key=lambda item: -item[0])
    for difference in range(90, 14, -1):
        chosen: list[Hct] = []
        for _, hct in scored:
            if all(_difference_degrees(hct.hue, c.hue) >= difference for c in chosen):
                chosen.append(hct)
            if len(chosen) >= 4:
                break
        if len(chosen) >= 4:
            break
    return chosen[0].argb if scored and chosen else fallback


def seed_from_pixels(pixels: Iterable[tuple[int, ...]], fallback: str = "#4285f4") -> str:
    """Исходный цвет по набору пикселей (итерируемое из (r, g, b) или
    (r, g, b, a)). Бесцветная картинка — fallback.

    Полупрозрачные пиксели пропускаются, как в оригинале: у значков это
    сглаженные края, и их цвет смешан с тем, что было под картинкой.
    """
    counts: dict[tuple[int, int, int], int] = {}
    translucent: dict[tuple[int, int, int], int] = {}
    for px in pixels:
        key = (px[0], px[1], px[2])
        target = counts if len(px) < 4 or px[3] >= 255 else translucent if px[3] >= 128 else None
        if target is not None:
            target[key] = target.get(key, 0) + 1
    if not counts:
        # Значок целиком полупрозрачный — лучше взять его, чем ничего.
        counts = translucent
    fallback_argb = _parse(fallback)
    if not counts:
        return _hex(fallback_argb)
    population = _Wu(counts).quantize(128)
    return _hex(_score(population, fallback_argb))


_ICON_SIDE = 64


def seed_from_png(path: str, fallback: str = "#4285f4") -> str:
    """То же по файлу значка. Картинка уменьшается до ~64×64.

    Читает GdkPixbuf: он есть в среде Merci и понимает любые форматы
    значков. Если его нет (тесты, чужая среда), выручает свой разбор PNG
    на zlib — он знает только обычные 8-битные PNG, но значки APK почти
    всегда такие.

    Нечитаемый значок — тоже fallback, а не исключение: из-за кривой
    картинки в чужом APK не должна падать отрисовка всей библиотеки.
    """
    import struct
    import zlib

    try:
        pixels = list(_pixbuf_pixels(path))
    except (ImportError, ValueError):
        try:
            pixels = list(_png_pixels(path))
        except (OSError, ValueError, IndexError, zlib.error, struct.error):
            return _hex(_parse(fallback))
    return seed_from_pixels(pixels, fallback)


def _pixbuf_pixels(path: str) -> Iterable[tuple[int, ...]]:
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, _ICON_SIDE, _ICON_SIDE, True)
    except Exception as exc:  # GLib.Error: битый файл или незнакомый формат
        raise ValueError(str(exc)) from exc
    data = pixbuf.get_pixels()
    channels = pixbuf.get_n_channels()
    stride = pixbuf.get_rowstride()
    width, height = pixbuf.get_width(), pixbuf.get_height()
    for y in range(height):
        row = y * stride
        for x in range(width):
            o = row + x * channels
            yield tuple(data[o:o + channels])


def _png_pixels(path: str) -> Iterable[tuple[int, ...]]:
    import struct
    import zlib

    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("не PNG")
    pos = 8
    idat = bytearray()
    palette = b""
    alpha = b""
    width = height = depth = color_type = interlace = 0
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"PLTE":
            palette = chunk
        elif kind == b"tRNS":
            alpha = chunk
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if depth != 8 or interlace or channels is None:
        raise ValueError("поддерживаются только 8-битные PNG без чересстрочности")
    raw = zlib.decompress(bytes(idat))
    bpp = channels
    stride = width * bpp
    rows: list[bytearray] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        kind = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        # Фильтры строк PNG: каждый байт задан разностью с соседями.
        if kind == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif kind == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[i] = (line[i] + pred) & 255
        rows.append(line)
        prev = line
    # Уменьшение выборкой через шаг: для поиска преобладающего цвета
    # усреднение не нужно, а прозрачные края так не смешиваются с фоном.
    step = max(1, max(width, height) // _ICON_SIDE)
    for y in range(0, height, step):
        line = rows[y]
        for x in range(0, width, step):
            o = x * bpp
            if color_type == 6:
                yield tuple(line[o:o + 4])
            elif color_type == 2:
                yield tuple(line[o:o + 3])
            elif color_type == 0:
                v = line[o]
                yield (v, v, v)
            elif color_type == 4:
                v = line[o]
                yield (v, v, v, line[o + 1])
            else:
                i = line[o]
                a = alpha[i] if i < len(alpha) else 255
                yield (palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2], a)
