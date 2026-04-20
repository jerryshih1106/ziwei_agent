import logging
import pandas as pd
from .base import DI_ZHI, PALACE_NAME, TIAN_GAN, ZiWeiConfig
from .other_star import OtherStarPosition
from .palace import generate_1year_luck, get_yan_shou_by_tian_gan, calculate_ming_palaces, get_zi_wei_xing_at_gong_wei, get_wsj, get_shi_si_zhu_xing_layout, generate_10year_luck
from .zwds_utils import gregorian_to_lunar, year_to_tian_gan_di_zhi

logger = logging.getLogger(__name__)

class ZiweiChart:
    def __init__(self, config:ZiWeiConfig):
        self.config = config
        self.star_positions = {}

    def preprocessing_config(self):
        self.config.hour_of_di_zhi = DI_ZHI[(self.config.hour+1) // 2]
        self.config.year_of_tian_gan_di_zhi = year_to_tian_gan_di_zhi(self.config.year)
        logger.debug("干支: %s 年, 生於 %s 時", self.config.year_of_tian_gan_di_zhi, self.config.hour_of_di_zhi)
        if not self.config.is_lunar:
            self.config.year, self.config.month, self.config.day = gregorian_to_lunar(self.config.year, self.config.month, self.config.day)
            logger.debug("轉換成農曆生日： %s年 %s月 %s日", self.config.year, self.config.month, self.config.day)

    def gen_chart(self):
        self.preprocessing_config()
        ming_palace, dizhi_palace_list, shen_palace = calculate_ming_palaces(self.config.month, self.config.hour)
        logger.debug("命宮: %s 身宮: %s", DI_ZHI[ming_palace], DI_ZHI[shen_palace])
        wsj = get_wsj((self.config.year_of_tian_gan_di_zhi[0]) + DI_ZHI[ming_palace])
        ziwei_palace = get_zi_wei_xing_at_gong_wei(self.config.day, wsj)
        logger.debug("紫薇宮: %s", ziwei_palace)
        # 計算十四正星的位置
        self.star_positions = get_shi_si_zhu_xing_layout(ziwei_palace)

        other_star_position = OtherStarPosition(self.config)
        other_star_dict = other_star_position.get_total_star_dict()
        self.add_star_in_horoscope(other_star_dict)

        # 取四化星
        si_hua_xing = other_star_position.get_shi_gan_si_hua_xing()

        # 取大限
        da_xian = generate_10year_luck(self.config.is_male, self.config.year_of_tian_gan_di_zhi[0], wsj, ming_palace)
        # 取小限
        xia_xian = generate_1year_luck(self.config.is_male, self.config.year_of_tian_gan_di_zhi[1])
        
        logger.debug("ming_palace: %s", ming_palace)
        tian_gan_list = get_yan_shou_by_tian_gan(self.config.year_of_tian_gan_di_zhi)

        palace_data = []
        for i in range(len(PALACE_NAME)):
            tian_gan = tian_gan_list[i]  # 假設天干按順序排列
            di_zhi = DI_ZHI[i]
            palace = dizhi_palace_list[i]
            stars = self.star_positions[di_zhi]
            hua = [hua for star in stars for hua in si_hua_xing.get(star, [])]
            palace_data.append({
                "宮": palace,
                "天干": tian_gan,
                "地支": di_zhi,
                "星": ", ".join(stars),
                "化": ", ".join(hua),
                "大限": da_xian[i],
                "小限": xia_xian[i],
            })

        return pd.DataFrame(palace_data)


    def add_star_in_horoscope(self, star_dict:dict):
        # 整合所有星位信息
        for k, v in star_dict.items():
            self.star_positions[v].add(k)
