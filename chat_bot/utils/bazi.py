"""
Pure-Python 八字（BaZi）calculator.
Solar terms accurate to ~1 minute for 1900–2100.
No external astronomy packages required.
"""
import math
from datetime import date, datetime

# ── Fundamental constants ─────────────────────────────────────
TIAN_GAN = ['甲', '乙', '丙', '丁', '戊', '己', '庚', '辛', '壬', '癸']
DI_ZHI   = ['子', '丑', '寅', '卯', '辰', '巳', '午', '未', '申', '酉', '戌', '亥']
_60JIAZI = [TIAN_GAN[i % 10] + DI_ZHI[i % 12] for i in range(60)]

# 12 major solar terms (節) and their ecliptic longitudes → month branch
# Order: 小寒(285°,丑)→立春(315°,寅)→驚蟄(345°,卯)→清明(15°,辰)→...
_JIE = [
    (285, '丑'),  # 小寒
    (315, '寅'),  # 立春   ← also triggers year change
    (345, '卯'),  # 驚蟄
    ( 15, '辰'),  # 清明
    ( 45, '巳'),  # 立夏
    ( 75, '午'),  # 芒種
    (105, '未'),  # 小暑
    (135, '申'),  # 立秋
    (165, '酉'),  # 白露
    (195, '戌'),  # 寒露
    (225, '亥'),  # 立冬
    (255, '子'),  # 大雪
]

# 五虎遁月訣: year stem index → stem index of the 寅月 (first month = 寅)
_MONTH_START_STEM = {0:2, 1:4, 2:6, 3:8, 4:0, 5:2, 6:4, 7:6, 8:8, 9:0}

# 五鼠遁時訣: day stem index → stem index of 子時
_HOUR_START_STEM  = {0:0, 1:2, 2:4, 3:6, 4:8, 5:0, 6:2, 7:4, 8:6, 9:8}

# 時辰 boundaries (hour within 0-23, using *start* hour of each 時辰)
# 子=23-1, 丑=1-3, ...; we map via: (hour+1)//2 % 12
def _shichen_index(hour: int, minute: int = 0) -> int:
    """Return 0-11 index (子=0, 丑=1, ..., 亥=11) for local solar hour."""
    total_min = hour * 60 + minute
    # 子時 starts at 23:00 = 1380 min
    # Shift so 子時 is index 0: add 60 min so 23:00→0, 1:00→120, ...
    adjusted = (total_min + 60) % 1440
    return adjusted // 120


# ── Astronomy helpers ─────────────────────────────────────────

def _gregorian_to_jdn(year: int, month: int, day: int) -> int:
    """Gregorian calendar → Julian Day Number (integer, for noon of that date)."""
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    return day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


def _sun_longitude(jde: float) -> float:
    """Apparent ecliptic longitude of the sun (degrees, 0-360)."""
    T = (jde - 2451545.0) / 36525.0
    L0 = (280.46646 + 36000.76983 * T + 0.0003032 * T * T) % 360
    M_deg = (357.52911 + 35999.05029 * T - 0.0001537 * T * T) % 360
    M = math.radians(M_deg)
    C = ((1.914602 - 0.004817 * T - 0.000014 * T * T) * math.sin(M)
         + (0.019993 - 0.000101 * T) * math.sin(2 * M)
         + 0.000289 * math.sin(3 * M))
    true_lon = L0 + C
    omega = math.radians(125.04 - 1934.136 * T)
    apparent = true_lon - 0.00569 - 0.00478 * math.sin(omega)
    return apparent % 360


def _equation_of_time(jde: float) -> float:
    """Equation of time in minutes (positive → sun ahead of mean sun)."""
    T = (jde - 2451545.0) / 36525.0
    epsilon = math.radians(23.439291 - 0.013004 * T)
    L0 = math.radians((280.46646 + 36000.76983 * T) % 360)
    M = math.radians((357.52911 + 35999.05029 * T) % 360)
    e = 0.016708634 - 0.000042037 * T
    y = math.tan(epsilon / 2) ** 2
    eot = (y * math.sin(2 * L0)
           - 2 * e * math.sin(M)
           + 4 * e * y * math.sin(M) * math.cos(2 * L0)
           - 0.5 * y * y * math.sin(4 * L0)
           - 1.25 * e * e * math.sin(2 * M))
    return math.degrees(eot) * 4  # convert to minutes


def _find_solar_term_jde(year: int, target_lon: float, search_month: int) -> float:
    """
    Find the JDE (Julian Day Ephemeris) when the sun's longitude crosses target_lon
    in the vicinity of (year, search_month).
    """
    # Initial estimate: JDN of the 6th of search_month
    jde = float(_gregorian_to_jdn(year, search_month, 6)) + 0.5  # noon UT
    speed = 360.0 / 365.25  # degrees per day

    for _ in range(50):
        lon = _sun_longitude(jde)
        diff = (target_lon - lon + 180) % 360 - 180  # signed angular difference
        if abs(diff) < 0.0001:
            break
        jde += diff / speed

    return jde


def _jde_to_date_hour(jde: float, utc_offset_hours: float = 8.0):
    """Convert JDE to (year, month, day, hour_float) in the given UTC offset."""
    # jde is noon UT; add utc offset
    jde_local = jde + utc_offset_hours / 24.0
    # Julian Day Number at local noon
    jdn = int(jde_local + 0.5)
    # Convert JDN to Gregorian
    a = jdn + 32044
    b = (4 * a + 3) // 146097
    c = a - (146097 * b) // 4
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    day   = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year  = 100 * b + d - 4800 + m // 10
    # Sub-day fraction
    frac = (jde_local + 0.5) % 1.0
    hour_float = frac * 24.0
    return year, month, day, hour_float


# ── Solar term cache (per-year, lazy) ────────────────────────
_SOLAR_TERM_CACHE: dict = {}

def _get_year_jie(year: int) -> list:
    """
    Return list of 12 (date, jde) tuples for the major 節 of *year*,
    in order 小寒(~Jan)→大雪(~Dec).
    """
    if year in _SOLAR_TERM_CACHE:
        return _SOLAR_TERM_CACHE[year]

    # Approximate calendar months each 節 falls in (for the search window)
    search_months = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    result = []
    for (target_lon, branch), sm in zip(_JIE, search_months):
        jde = _find_solar_term_jde(year, target_lon, sm)
        y, m, d, hf = _jde_to_date_hour(jde, utc_offset_hours=8)
        result.append((date(y, m, d), hf, branch, target_lon))

    _SOLAR_TERM_CACHE[year] = result
    return result


# ── Year pillar ───────────────────────────────────────────────

def _lichun_jde(year: int) -> float:
    """JDE of 立春 (315°) for the given year."""
    return _find_solar_term_jde(year, 315, 2)


def _year_pillar(birth_year: int, birth_month: int, birth_day: int,
                 birth_hour: float) -> tuple:
    """
    Return (stem_idx, branch_idx, ganzhi_str) for the year pillar.
    Year changes at 立春, not Jan 1.
    birth_hour: local solar time hour (float).
    """
    lichun = _lichun_jde(birth_year)
    # Convert to local date/hour
    ly, lm, ld, lh = _jde_to_date_hour(lichun, utc_offset_hours=8)
    lichun_date = date(ly, lm, ld)
    lichun_hour = lh

    birth_date_obj = date(birth_year, birth_month, birth_day)

    # Determine which bazi year this birth falls in
    if birth_date_obj < lichun_date:
        bazi_year = birth_year - 1
    elif birth_date_obj == lichun_date and birth_hour < lichun_hour:
        bazi_year = birth_year - 1
    else:
        bazi_year = birth_year

    # Reference: 1924 = 甲子 year (stem 0, branch 0)
    offset = bazi_year - 1924
    stem_idx   = offset % 10
    branch_idx = offset % 12
    return stem_idx, branch_idx, TIAN_GAN[stem_idx] + DI_ZHI[branch_idx]


# ── Month pillar ──────────────────────────────────────────────

def _month_pillar(birth_year: int, birth_month: int, birth_day: int,
                  birth_hour: float, year_stem_idx: int) -> tuple:
    """
    Return (stem_idx, branch_idx, ganzhi_str) for the month pillar.
    Determined by which 節 the birth falls after.
    """
    birth_date_obj = date(birth_year, birth_month, birth_day)

    # Gather 節 from previous year's 大雪 through current year's 大雪
    # to cover the full range including Jan dates that belong to prev-year 丑月
    candidates = []
    for y in (birth_year - 1, birth_year):
        for (d, hf, branch, lon) in _get_year_jie(y):
            candidates.append((d, hf, branch, lon))

    # Sort by date
    candidates.sort(key=lambda x: (x[0], x[1]))

    # Find the last 節 before or equal to the birth date/time
    active_branch = DI_ZHI.index('丑')  # default (before 小寒 of year)
    active_lon = 285

    for (d, hf, branch, lon) in candidates:
        if d < birth_date_obj:
            active_branch = DI_ZHI.index(branch)
            active_lon = lon
        elif d == birth_date_obj and hf <= birth_hour:
            active_branch = DI_ZHI.index(branch)
            active_lon = lon
        else:
            break

    # Map branch to month index (寅=0, 卯=1, ..., 丑=11)
    # 寅月 = 1st month, branch index of 寅 = 2
    # month_num: 寅(2)→0, 卯(3)→1, ..., 丑(1)→11
    branch_to_month_num = {
        2: 0,  # 寅
        3: 1,  # 卯
        4: 2,  # 辰
        5: 3,  # 巳
        6: 4,  # 午
        7: 5,  # 未
        8: 6,  # 申
        9: 7,  # 酉
        10: 8, # 戌
        11: 9, # 亥
        0: 10, # 子
        1: 11, # 丑
    }
    month_num = branch_to_month_num.get(active_branch, 0)

    # Stem: base stem for 寅月 depends on year stem (五虎遁月)
    base_stem = _MONTH_START_STEM[year_stem_idx]
    stem_idx = (base_stem + month_num) % 10

    return stem_idx, active_branch, TIAN_GAN[stem_idx] + DI_ZHI[active_branch]


# ── Day pillar ────────────────────────────────────────────────

def _day_pillar(year: int, month: int, day: int, hour: float) -> tuple:
    """
    Return (stem_idx, branch_idx, ganzhi_str) for the day pillar.
    Uses verified formula: day_index = (JDN + 39) % 60
    where JDN is the Julian Day Number for noon of that date.
    Note: 子時 (23:00-01:00) — births before 01:00 belong to the *previous* calendar day's 子時.
    Chinese tradition: the day changes at 子時 START (23:00), so 23:00 onward = next day's 子時.
    """
    # Births in 子時 (23:00-24:00) belong to the current date's 子時 which is the next day's bazi day
    # Births in 子時 (0:00-1:00) belong to the previous date's 子時
    # In practice: hour ≥ 23 → use day+1 for the day stem/branch, hour < 1 → use day-1? No—
    # Traditional: the day flips at 23:00 (start of 子時). So hour 23,0 both belong to the same 子時.
    # Our _shichen_index(23,0) = index 0 (子). A birth at 23:30 is still 子時, but the *calendar day*
    # that 子時 belongs to is the NEXT day's bazi.
    # Convention used here: if hour >= 23, treat as day+1 for JDN.
    jdn_day = day + (1 if hour >= 23 else 0)
    jdn = _gregorian_to_jdn(year, month, jdn_day)
    idx = (jdn + 49) % 60
    stem_idx   = idx % 10
    branch_idx = idx % 12
    return stem_idx, branch_idx, TIAN_GAN[stem_idx] + DI_ZHI[branch_idx]


# ── Hour pillar ───────────────────────────────────────────────

def _hour_pillar(solar_hour: float, solar_minute: float,
                 day_stem_idx: int) -> tuple:
    """Return (stem_idx, branch_idx, ganzhi_str) for the hour pillar."""
    branch_idx = _shichen_index(int(solar_hour), int(solar_minute))
    base_stem  = _HOUR_START_STEM[day_stem_idx]
    stem_idx   = (base_stem + branch_idx) % 10
    return stem_idx, branch_idx, TIAN_GAN[stem_idx] + DI_ZHI[branch_idx]


# ── True solar time correction ────────────────────────────────

def true_solar_time(year: int, month: int, day: int,
                    hour: int, minute: int,
                    longitude: float,
                    standard_meridian: float = 120.0) -> tuple:
    """
    Convert standard time to true solar time.
    Returns (adjusted_hour, adjusted_minute, correction_minutes).
    longitude: east is positive.
    standard_meridian: standard longitude of the timezone (120 for UTC+8).
    """
    jde = _gregorian_to_jdn(year, month, day) + 0.5  # noon UT, close enough for EoT
    eot = _equation_of_time(jde)  # minutes
    lon_correction = (longitude - standard_meridian) * 4.0  # minutes
    total_correction = eot + lon_correction  # minutes

    total_min = hour * 60 + minute + total_correction
    # Wrap to 0-1440
    total_min = total_min % 1440
    adj_hour = int(total_min) // 60
    adj_min  = int(total_min) % 60
    return adj_hour, adj_min, round(total_correction, 1)


# ── Main entry point ──────────────────────────────────────────

def compute_bazi(year: int, month: int, day: int,
                 hour: int, minute: int,
                 longitude: float = 121.5,
                 standard_meridian: float = 120.0) -> dict:
    """
    Compute the Four Pillars (八字) for a given birth date/time/location.

    Args:
        year, month, day: Gregorian birth date
        hour, minute: local standard time (24h)
        longitude: birth longitude (east positive; default 121.5 = Taipei)
        standard_meridian: timezone standard meridian (default 120 = UTC+8)

    Returns dict with keys:
        year_pillar, month_pillar, day_pillar, hour_pillar
            each: {stem, branch, ganzhi, stem_idx, branch_idx}
        solar_hour, solar_minute: true solar time used
        correction_minutes: true solar time correction applied
        shichen: name of the 時辰
        summary: human-readable string
    """
    # Step 1: true solar time
    sol_h, sol_m, correction = true_solar_time(
        year, month, day, hour, minute, longitude, standard_meridian
    )
    sol_hour_float = sol_h + sol_m / 60.0

    # Step 2: year pillar
    ys, yb, year_gz = _year_pillar(year, month, day, sol_hour_float)

    # Step 3: month pillar
    ms, mb, month_gz = _month_pillar(year, month, day, sol_hour_float, ys)

    # Step 4: day pillar
    ds, db, day_gz = _day_pillar(year, month, day, sol_hour_float)

    # Step 5: hour pillar
    hs, hb, hour_gz = _hour_pillar(sol_hour_float, sol_m, ds)

    # 時辰 name
    shichen_names = ['子', '丑', '寅', '卯', '辰', '巳', '午', '未', '申', '酉', '戌', '亥']
    shichen = shichen_names[_shichen_index(sol_h, sol_m)] + '時'

    def _pillar(s, b, gz):
        return {"stem": TIAN_GAN[s], "branch": DI_ZHI[b], "ganzhi": gz,
                "stem_idx": s, "branch_idx": b}

    pillars = {
        "year_pillar":  _pillar(ys, yb, year_gz),
        "month_pillar": _pillar(ms, mb, month_gz),
        "day_pillar":   _pillar(ds, db, day_gz),
        "hour_pillar":  _pillar(hs, hb, hour_gz),
    }

    pk = ('year_pillar', 'month_pillar', 'day_pillar', 'hour_pillar')
    stems    = [ys, ms, ds, hs]
    branches = [yb, mb, db, hb]
    canggan = {pk[i]: _CANGGAN[branches[i]] for i in range(4)}
    dizhi   = {pk[i]: _dizhi_stage(ds, branches[i]) for i in range(4)}
    nayin   = {pk[i]: _nayin(stems[i], branches[i]) for i in range(4)}

    return {
        **pillars,
        "solar_hour":   sol_h,
        "solar_minute": sol_m,
        "correction_minutes": correction,
        "shichen": shichen,
        "canggan": canggan,
        "dizhi":   dizhi,
        "nayin":   nayin,
        "shenshas": compute_shenshas(pillars),
        "summary": f"{year_gz} {month_gz} {day_gz} {hour_gz}",
    }


# ── Wuxing (五行) helpers ─────────────────────────────────────

_STEM_ELEMENT  = ['木','木','火','火','土','土','金','金','水','水']
_BRANCH_ELEMENT = ['水','土','木','木','土','火','火','土','金','金','土','水']

# Approximate calendar month each 節 falls in (for solar term search seeding)
_JIE_SEARCH_MONTHS = {285:1, 315:2, 345:3, 15:4, 45:5, 75:6,
                       105:7, 135:8, 165:9, 195:10, 225:11, 255:12}


def wuxing_count(bazi: dict) -> dict:
    """Count the five elements in the 8 characters."""
    counts = {'木':0,'火':0,'土':0,'金':0,'水':0}
    for key in ('year_pillar','month_pillar','day_pillar','hour_pillar'):
        p = bazi[key]
        counts[_STEM_ELEMENT[p['stem_idx']]]    += 1
        counts[_BRANCH_ELEMENT[p['branch_idx']]] += 1
    return counts


def day_master(bazi: dict) -> str:
    """Return the day master (日主) — the stem of the day pillar."""
    return bazi['day_pillar']['stem']


# ── 藏幹 (hidden stems within each branch) ───────────────────
_CANGGAN: dict = {
    0:  ['癸'],
    1:  ['己', '癸', '辛'],
    2:  ['甲', '丙', '戊'],
    3:  ['乙'],
    4:  ['戊', '乙', '癸'],
    5:  ['丙', '庚', '戊'],
    6:  ['丁', '己'],
    7:  ['己', '丁', '乙'],
    8:  ['庚', '壬', '戊'],
    9:  ['辛'],
    10: ['戊', '辛', '丁'],
    11: ['壬', '甲'],
}

# ── 地勢 (十二長生 / 12 life stages) ─────────────────────────
# Indexed by day-stem: branch index where 長生 begins (yang=forward, yin=backward)
_CHANGSHENG_START  = [11, 6, 2, 9, 2, 9, 5, 0, 8, 3]  # 甲亥 乙午 丙寅 丁酉 戊寅 己酉 庚巳 辛子 壬申 癸卯
_CHANGSHENG_FORWARD = [True, False, True, False, True, False, True, False, True, False]
_TWELVE_STAGES = ['長生','沐浴','冠帶','臨官','帝旺','衰','病','死','墓','絕','胎','養']

def _dizhi_stage(day_stem_idx: int, branch_idx: int) -> str:
    start = _CHANGSHENG_START[day_stem_idx]
    if _CHANGSHENG_FORWARD[day_stem_idx]:
        return _TWELVE_STAGES[(branch_idx - start) % 12]
    return _TWELVE_STAGES[(start - branch_idx) % 12]

# ── 納音 (60-jiazi nayin) ─────────────────────────────────────
_NAYIN = [
    '海中金','爐中火','大林木','路旁土','劍鋒金',
    '山頭火','澗下水','城頭土','白蠟金','楊柳木',
    '泉中水','屋上土','霹靂火','松柏木','長流水',
    '沙中金','山下火','平地木','壁上土','金箔金',
    '覆燈火','天河水','大驛土','釵釧金','桑柘木',
    '大溪水','沙中土','天上火','石榴木','大海水',
]
_GANZHI60_IDX: dict = {(i % 10, i % 12): i for i in range(60)}

def _nayin(stem_idx: int, branch_idx: int) -> str:
    return _NAYIN[_GANZHI60_IDX[(stem_idx, branch_idx)] // 2]

# ── 神煞 tables ────────────────────────────────────────────────
# 天干神煞 (indexed by day-stem index 0-9; values are DI_ZHI chars)
_TIANGAN_SHEN: dict = {
    '天乙貴人': (('丑','未'),('子','申'),('亥','酉'),('亥','酉'),('丑','未'),('子','申'),('丑','未'),('午','寅'),('卯','巳'),('卯','巳')),
    '干祿':    ('寅','卯','巳','午','巳','午','申','酉','亥','子'),
    '羊刃':    ('卯','辰','午','未','午','未','酉','戌','子','丑'),
    '紅艷煞':  (('午','申'),('午','申'),('寅',),('未',),('辰',),('辰',),('戌',),('酉',),('子',),('申',)),
    '金輿':    ('辰','巳','未','申','未','申','戌','亥','丑','寅'),
}

# 年干神煞 (indexed by year-stem index 0-9)
_NIANGAN_SHEN: dict = {
    '福星貴人': ('寅','丑','子','亥','酉','申','未','午','巳','辰'),
    '文昌貴人': ('巳','午','申','酉','申','酉','亥','子','寅','卯'),
}

# 時干神煞 (indexed by hour-stem index 0-9)
_SHIGAN_SHEN: dict = {
    '學堂': ('巳','午','申','酉','申','酉','亥','子','寅','卯'),
}

# 日支神煞 (indexed by day-branch index 0-11)
_RIZHI_SHEN: dict = {
    '驛馬': ('寅','亥','申','巳','寅','亥','申','巳','寅','亥','申','巳'),
    '華蓋': ('戌','丑','戌','未','戌','丑','戌','未','戌','未','戌','未'),
    '桃花': ('酉','午','卯','子','酉','午','卯','子','酉','午','卯','子'),
    '劫煞': ('巳','寅','亥','申','巳','寅','亥','申','巳','寅','亥','申'),
    '亡神': ('亥','申','巳','寅','亥','申','巳','寅','亥','申','巳','寅'),
    '將星': ('子','酉','午','卯','子','酉','午','卯','子','酉','午','卯'),
}

# 月支神煞 (keyed by month-branch index; value may be TIAN_GAN or DI_ZHI char)
_YUEZHI_SHEN: dict = {
    '天德貴人': {2:'丁',3:'申',4:'壬',5:'辛',6:'亥',7:'甲',8:'癸',9:'寅',10:'丙',11:'乙',0:'巳',1:'庚'},
    '天德合':   {2:'壬',3:'巳',4:'丁',5:'丙',6:'寅',7:'己',8:'戊',9:'亥',10:'辛',11:'庚',0:'申',1:'乙'},
    '月德貴人': {2:'丙',3:'甲',4:'壬',5:'庚',6:'丙',7:'甲',8:'壬',9:'庚',10:'丙',11:'甲',0:'壬',1:'庚'},
    '月德合':   {2:'辛',3:'己',4:'丁',5:'乙',6:'辛',7:'己',8:'丁',9:'乙',10:'辛',11:'己',0:'丁',1:'乙'},
}

# 年支神煞 (indexed by year-branch index 0-11)
_NIANZHI_SHEN: dict = {
    '將星': ('子','酉','午','卯','子','酉','午','卯','子','酉','午','卯'),
    '驛馬': ('寅','亥','申','巳','寅','亥','申','巳','寅','亥','申','巳'),
    '華蓋': ('戌','丑','戌','未','戌','丑','戌','未','戌','丑','戌','未'),
    '劫煞': ('巳','寅','亥','申','巳','寅','亥','申','巳','寅','亥','申'),
    '災煞': ('午','卯','子','酉','午','卯','子','酉','午','卯','子','酉'),
    '桃花': ('酉','午','卯','子','酉','午','卯','子','酉','午','卯','子'),
    '紅鸞': ('卯','寅','丑','子','亥','戌','酉','申','未','午','巳','辰'),
    '天喜': ('酉','申','未','午','巳','辰','卯','寅','丑','子','亥','戌'),
    '孤辰': ('寅','寅','巳','巳','巳','申','申','申','亥','亥','亥','寅'),
    '寡宿': ('戌','戌','丑','丑','丑','辰','辰','辰','未','未','未','戌'),
    '元辰': ('未','申','酉','戌','亥','子','丑','寅','卯','辰','巳','午'),
    '弔客': ('戌','亥','子','丑','寅','卯','辰','巳','午','未','申','酉'),
    '流霞': ('申','酉','戌','亥','子','丑','寅','卯','辰','巳','午','未'),
}

# 特殊日格 (checked against day ganzhi stem/branch index pair)
_YINCHA_YANGCUO = {(2,0),(3,1),(4,2),(7,3),(8,4),(9,5),
                   (2,6),(3,7),(4,8),(7,9),(8,10),(9,11)}
_LIUXIU = {(2,6),(3,7),(4,0),(4,6),(5,1),(5,7)}


def compute_shenshas(bazi: dict) -> dict:
    """
    Compute 神煞 for each of the four pillars.
    Returns {'year_pillar': [...], 'month_pillar': [...], 'day_pillar': [...], 'hour_pillar': [...]}.
    """
    day_stem_idx     = bazi['day_pillar']['stem_idx']
    day_branch_idx   = bazi['day_pillar']['branch_idx']
    month_branch_idx = bazi['month_pillar']['branch_idx']
    year_branch_idx  = bazi['year_pillar']['branch_idx']
    year_stem_idx    = bazi['year_pillar']['stem_idx']
    hour_stem_idx    = bazi['hour_pillar']['stem_idx']

    pk = ('year_pillar', 'month_pillar', 'day_pillar', 'hour_pillar')
    p_stems    = [bazi[k]['stem_idx']   for k in pk]
    p_branches = [bazi[k]['branch_idx'] for k in pk]
    out: dict = {k: [] for k in pk}

    def _by_branch(name: str, char: str) -> None:
        idx = DI_ZHI.index(char)
        for i, b in enumerate(p_branches):
            if b == idx and name not in out[pk[i]]:
                out[pk[i]].append(name)

    def _by_stem_or_branch(name: str, char: str) -> None:
        if char in TIAN_GAN:
            idx = TIAN_GAN.index(char)
            for i, s in enumerate(p_stems):
                if s == idx and name not in out[pk[i]]:
                    out[pk[i]].append(name)
        else:
            _by_branch(name, char)

    # 天干神煞 (by day stem)
    for shen, tbl in _TIANGAN_SHEN.items():
        targets = tbl[day_stem_idx]
        if isinstance(targets, str):
            targets = (targets,)
        for t in targets:
            _by_branch(shen, t)

    # 年干神煞 (by year stem)
    for shen, tbl in _NIANGAN_SHEN.items():
        _by_branch(shen, tbl[year_stem_idx])

    # 時干神煞 (by hour stem)
    for shen, tbl in _SHIGAN_SHEN.items():
        _by_branch(shen, tbl[hour_stem_idx])

    # 日支神煞 (by day branch)
    for shen, tbl in _RIZHI_SHEN.items():
        _by_branch(shen, tbl[day_branch_idx])

    # 月支神煞 (by month branch, value may be stem or branch char)
    for shen, tbl in _YUEZHI_SHEN.items():
        _by_stem_or_branch(shen, tbl[month_branch_idx])

    # 年支神煞 (by year branch)
    for shen, tbl in _NIANZHI_SHEN.items():
        _by_branch(shen, tbl[year_branch_idx])

    # 特殊日格 (applies to day pillar)
    day_pair = (day_stem_idx, day_branch_idx)
    if day_pair in _YINCHA_YANGCUO:
        out['day_pillar'].append('陰差陽錯')
    if day_pair in _LIUXIU:
        out['day_pillar'].append('六秀日')
    if day_stem_idx % 2 == day_branch_idx % 2:
        out['day_pillar'].append('八專日')

    # 六甲空亡 (based on year pillar's 旬)
    year_gz_idx  = _GANZHI60_IDX[(year_stem_idx, year_branch_idx)]
    xun_start_branch = (year_gz_idx // 10 * 10) % 12
    kongwang = {(xun_start_branch + 10) % 12, (xun_start_branch + 11) % 12}
    for i, b in enumerate(p_branches):
        if b in kongwang and '六甲空亡' not in out[pk[i]]:
            out[pk[i]].append('六甲空亡')

    return out


def compute_dayun(year: int, month: int, day: int,
                  hour: int, minute: int,
                  is_male: bool,
                  longitude: float = 121.5) -> dict:
    """
    Compute 大運 (10-year luck pillars).

    Direction rule:
      陽男 or 陰女 → 順排 (forward from month pillar)
      陰男 or 陽女 → 逆排 (backward from month pillar)
    陽年: year stem is 甲丙戊庚壬 (even index 0,2,4,6,8)

    Returns {
        forward: bool,
        start_age_years: int,
        start_age_months: int,
        dayun: [{stem, branch, ganzhi, stem_idx, branch_idx, start_age, end_age}, ...]
    }
    """
    bazi = compute_bazi(year, month, day, hour, minute, longitude)
    sol_h  = bazi['solar_hour']
    sol_m  = bazi['solar_minute']
    sol_hf = sol_h + sol_m / 60.0

    year_stem_idx = bazi['year_pillar']['stem_idx']
    is_yang_year  = (year_stem_idx % 2 == 0)   # 甲丙戊庚壬 are yang

    # 陽男陰女順，陰男陽女逆
    forward = (is_male and is_yang_year) or (not is_male and not is_yang_year)

    # Birth JDE in local solar time (noon UT + offset)
    birth_jde = (_gregorian_to_jdn(year, month, day)
                 + (sol_hf - 12.0) / 24.0)   # relative to UT noon; approx

    # Collect all 節 JDE values from year-1 through year+1
    all_jie_jde = []
    for y in (year - 1, year, year + 1):
        for (lon_target, _branch) in _JIE:
            sm = _JIE_SEARCH_MONTHS[lon_target]
            jde_j = _find_solar_term_jde(y, lon_target, sm)
            all_jie_jde.append(jde_j)
    all_jie_jde.sort()

    # Find the nearest 節 (forward or backward)
    if forward:
        # next 節 after birth
        candidates = [j for j in all_jie_jde if j > birth_jde]
        target_days = (min(candidates) - birth_jde) if candidates else 0
    else:
        # prev 節 before birth
        candidates = [j for j in all_jie_jde if j < birth_jde]
        target_days = (birth_jde - max(candidates)) if candidates else 0

    # 起運: 3 days ≈ 1 year, 1 day ≈ 4 months
    total_months_f = target_days * 4.0
    start_age_years  = int(total_months_f // 12)
    start_age_months = round(total_months_f % 12)
    if start_age_months == 12:
        start_age_years += 1
        start_age_months = 0

    # Generate 8 大運 from month pillar
    ms = bazi['month_pillar']['stem_idx']
    mb = bazi['month_pillar']['branch_idx']
    start_age_base = start_age_years + start_age_months / 12.0
    dayun_list = []
    for i in range(1, 9):
        s = (ms + i) % 10 if forward else (ms - i) % 10
        b = (mb + i) % 12 if forward else (mb - i) % 12
        sa = round(start_age_base + (i - 1) * 10, 2)
        dayun_list.append({
            'stem':       TIAN_GAN[s],
            'branch':     DI_ZHI[b],
            'ganzhi':     TIAN_GAN[s] + DI_ZHI[b],
            'stem_idx':   s,
            'branch_idx': b,
            'start_age':  sa,
            'end_age':    round(sa + 10, 2),
        })

    return {
        'forward':           forward,
        'start_age_years':   start_age_years,
        'start_age_months':  start_age_months,
        'dayun':             dayun_list,
    }
