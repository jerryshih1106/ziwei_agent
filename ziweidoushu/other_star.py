from .base import FOUR_HUA, HUA_DICT, TIAN_GAN, DI_ZHI, ZiWeiConfig

class OtherStarPosition:
    def __init__(self, config:ZiWeiConfig):
        self.config = config
        self.total_star = {}
    def get_total_star_dict(self):
        self.get_fu_bi_xing_at_di_zhi()
        self.get_wen_chang_wen_qu_at_di_zhi()
        self.get_qin_yang_tuo_luo_at_di_zhi()
        self.get_tian_kui_yue()
        self.get_huo_ling_star_positions()
        self.get_tian_kong()
        self.get_di_jie()
        self.get_tai_fu()
        self.get_feng_gao()
        return self.total_star

    # 取左輔, 右弼
    def get_fu_bi_xing_at_di_zhi(self):
        # 左辅星地支
        zuo_fu_xing_di_zhi = DI_ZHI[(self.config.month + 3) % 12]

        # 右弼星地支
        you_bi_fu_xing_di_zhi = DI_ZHI[(11 - self.config.month) % 12]

        self.total_star.update({"左輔": zuo_fu_xing_di_zhi, "右弼": you_bi_fu_xing_di_zhi})

    # 取文昌, 文曲
    def get_wen_chang_wen_qu_at_di_zhi(self):
        hour_no = DI_ZHI.index(self.config.hour_of_di_zhi) + 1
        wen_qu_di_zhi = DI_ZHI[(3 + hour_no) % 12]

        # 文昌地支
        wen_chang_di_zhi = DI_ZHI[(11 - hour_no) % 12]
        self.total_star.update({"文曲": wen_qu_di_zhi, "文昌": wen_chang_di_zhi})

    def get_qin_yang_tuo_luo_at_di_zhi(self):
        """
        安置（擎羊、祿存、陀羅）的位置，根據出生月。
        month_no: 出生月（1 ~ 12），Python index 請傳入 0 ~ 11
        """
        cur_dict = {
            "甲": "寅",
            "乙": "卯",
            "丙": "巳",
            "戊": "巳",
            "丁": "午",
            "己": "午",
            "庚": "申",
            "辛": "酉",
            "壬": "亥",
            "癸": "子",
        }

        # 祿存：
        lu_chuen_di_zhi = cur_dict.get(self.config.year_of_tian_gan_di_zhi[0])

        # 擎羊：從丑起順行，依月份加
        qing_yang_di_zhi = DI_ZHI[DI_ZHI.index(lu_chuen_di_zhi)+1]  # 丑 = index 1

        # 陀羅：從未起逆行，依月份減
        tuo_luo_di_zhi = DI_ZHI[DI_ZHI.index(lu_chuen_di_zhi)-1]  # 未 = index 7

        self.total_star.update({
            "擎羊": qing_yang_di_zhi,
            "祿存": lu_chuen_di_zhi,
            "陀羅": tuo_luo_di_zhi
        })

    def get_tian_kui_yue(self):
        """
        根據出生年天干，安置天魁與天鉞。
        year_of_tian_gan: 天干字串，例如 "甲"
        """
        gui_ren_mapping = {
            "甲": ("丑", "未"),
            "戊": ("丑", "未"),
            "庚": ("丑", "未"),
            "乙": ("子", "申"),
            "己": ("子", "申"),
            "辛": ("午", "寅"),
            "丙": ("亥", "酉"),
            "丁": ("亥", "酉"),
            "壬": ("卯", "巳"),
            "癸": ("卯", "巳"),
        }

        result = gui_ren_mapping.get(self.config.year_of_tian_gan_di_zhi[0])

        tian_kui, tian_yue = result
        self.total_star.update({
            "天魁": tian_kui,
            "天鉞": tian_yue
        })

    def get_huo_ling_star_positions(self):
        """
        安置火星與鈴星位置
        year_of_di_zhi: 出生年的地支（如 "寅"）
        hour_of_di_zhi: 出生時辰的地支（如 "子"）
        """
        huo_start_mapping = {
            "寅": "丑", "午": "丑", "戌": "丑",
            "申": "寅", "子": "寅", "辰": "寅",
            "巳": "卯", "酉": "卯", "丑": "卯",
            "亥": "酉", "卯": "酉", "未": "酉"
        }

        ling_start_mapping = {
            "寅": "卯", "午": "卯", "戌": "卯",
            "申": "戌", "子": "戌", "辰": "戌",
            "巳": "戌", "酉": "戌", "丑": "戌",
            "亥": "戌", "卯": "戌", "未": "戌"
        }

        # 火星起始宮
        huo_start = huo_start_mapping[self.config.year_of_tian_gan_di_zhi[1]]
        # 鈴星起始宮
        ling_start = ling_start_mapping[self.config.year_of_tian_gan_di_zhi[1]]

        # 計算位置
        huo_index = (DI_ZHI.index(huo_start) + DI_ZHI.index(self.config.hour_of_di_zhi)) % 12
        ling_index = (DI_ZHI.index(ling_start) + DI_ZHI.index(self.config.hour_of_di_zhi)) % 12

        huo_position = DI_ZHI[huo_index]
        ling_position = DI_ZHI[ling_index]

        self.total_star.update({
            "火星": huo_position,
            "鈴星": ling_position
        })

    def get_tian_kong(self):
        """
        安天空星：亥宮起子時，逆數至生時
        """
        start_index = DI_ZHI.index("亥")
        offset = DI_ZHI.index(self.config.hour_of_di_zhi)
        index = (start_index - offset) % 12
        self.total_star.update({"地空":DI_ZHI[index]})

    def get_di_jie(self):
        """
        安地劫星：亥宮起子時，順數至生時
        """
        start_index = DI_ZHI.index("亥")
        offset = DI_ZHI.index(self.config.hour_of_di_zhi)
        index = (start_index + offset) % 12
        self.total_star.update({"地劫":DI_ZHI[index]})

    def get_tai_fu(self):
        """
        安台輔星：午宮起子時，順數至生時
        """
        start_index = DI_ZHI.index("午")
        offset = DI_ZHI.index(self.config.hour_of_di_zhi)
        index = (start_index + offset) % 12
        self.total_star.update({"台輔":DI_ZHI[index]})

    def get_feng_gao(self):
        """
        安封誥星：寅宮起子時，順數至生時
        """
        start_index = DI_ZHI.index("寅")
        offset = DI_ZHI.index(self.config.hour_of_di_zhi)
        index = (start_index + offset) % 12
        self.total_star.update({"封誥":DI_ZHI[index]})

    #取四化星
    def get_shi_gan_si_hua_xing(self):
        return {HUA_DICT.get(self.config.year_of_tian_gan_di_zhi[0])[i]: FOUR_HUA[i] for i in range(4)}
