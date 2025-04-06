from lunarcalendar import Converter
from collections import namedtuple
from .base import DI_ZHI, TIAN_GAN

def gregorian_to_lunar(year: int, month: int, day: int) -> tuple[int]:
    # 使用 Converter.Solar2Lunar 方法进行转换
    solar_date = namedtuple('Solar', ['year', 'month', 'day'])
    solar = solar_date(year, month, day)
    
    # 获取农历日期
    lunar = Converter.Solar2Lunar(solar)
    
    # 格式化输出农历日期
    return lunar.year, lunar.month, lunar.day

def year_to_tian_gan_di_zhi(year: int) -> str:
    # 計算天干和地支的起始位置（西元 1984 年為甲子年）
    base_year = 1924
    tian_gan_index = (year - base_year) % 10
    di_zhi_index = (year - base_year) % 12
    # 返回對應的天干地支
    return TIAN_GAN[tian_gan_index] + DI_ZHI[di_zhi_index]