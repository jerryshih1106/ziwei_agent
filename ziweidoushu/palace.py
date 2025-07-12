from .base import XIAO_XIEN_START, PALACE_NAME, WSJ_DICT, WUXING_JU_MAP, LUNAR_DAY_TABLE, DI_ZHI, TIAN_FU_POSITION, FOUR_HUA, HUA_DICT, TIAN_GAN


def get_zi_wei_xing_at_gong_wei(nongli_day: int, wu_xing_ju: str) -> str:

    # 获取日期和五行局的索引
    row_idx = nongli_day - 1
    cln_idx = WUXING_JU_MAP[wu_xing_ju]

    # 返回結果
    return LUNAR_DAY_TABLE[row_idx][cln_idx]

def calculate_ming_palaces(birth_month, birth_hour):
    # Calculate the Ming Palace
    ming_palace = ((2 + birth_month - 1) - (birth_hour + 1) // 2) % 12
    dizhi_palace_list = []
    for i in range(len(PALACE_NAME)):
        dizhi_palace_list.append(PALACE_NAME[ming_palace - i])
    # Calculate the Shen Palace
    shen_palace = ((2 + birth_month - 1) + (birth_hour + 1) // 2) % 12
    return ming_palace, dizhi_palace_list, shen_palace

# 五行局
def get_wsj(s:str) -> str:
    for k, v in WSJ_DICT.items():
        if s[0] in k:
            for k_1, v_1 in v.items():
                if s[1] in k_1:
                    print("五行局: ", v_1)
                    return v_1
# 排 14 星          
def get_shi_si_zhu_xing_layout(zi_wei_at_di_zhi):
    # 定义地支宫位字典
    di_zhi_gong_wei = {item: set() for item in DI_ZHI}

    # 获取紫微和天府的位置
    zi_wei_di_zhi_idx = DI_ZHI.index(zi_wei_at_di_zhi)
    tian_fu_di_zhi_idx = DI_ZHI.index(TIAN_FU_POSITION[zi_wei_at_di_zhi])

    # 紫微星布局
    zi_wei_series_layout = ["紫薇", "", "", "", "廉貞", "", "", "天同", "武曲", "太陽", "", "天機"]

    # 天府星布局
    tian_fu_series_layout = ["天府", "太陰", "貪狼", "巨門", "天相", "天梁", "七殺", "", "", "", "破軍", ""]

    # 填充紫微布局
    for item in zi_wei_series_layout:
        if item:  # 忽略空项
            if zi_wei_di_zhi_idx < 11:
                di_zhi_gong_wei[DI_ZHI[zi_wei_di_zhi_idx]].add(item)
            di_zhi_gong_wei[DI_ZHI[zi_wei_di_zhi_idx % 12]].add(item)
        zi_wei_di_zhi_idx += 1

    # 填充天府布局
    for item in tian_fu_series_layout:
        if item:  # 忽略空项
            if tian_fu_di_zhi_idx < 11:
                di_zhi_gong_wei[DI_ZHI[tian_fu_di_zhi_idx]].add(item)
            di_zhi_gong_wei[DI_ZHI[tian_fu_di_zhi_idx % 12]].add(item)
        tian_fu_di_zhi_idx += 1

    return di_zhi_gong_wei

def get_yan_shou_by_tian_gan(tian_gan: str) -> list:
    y_dict = {
        "丙": "甲己",
        "戊": "乙庚",
        "庚": "丙辛",
        "壬": "丁壬",
        "甲": "戊癸"
    }
    
    year_tian_gan = tian_gan[0]  # 获取天干的第一个字

    # 查找并返回包含该天干字符的字典键
    for key, value in y_dict.items():
        if year_tian_gan in value:
            ind = TIAN_GAN.index(key)
            ming_sort_list = [TIAN_GAN[(ind + i) % 10] for i in range(12)]
            return ming_sort_list[-2:] + ming_sort_list[:-2]

def is_forward_direction(is_male: str, tian_gan: str) -> bool:
    if is_male == True:
        odd_tian_gan = [TIAN_GAN[i] for i in range(0,10,2)]
        return tian_gan in odd_tian_gan  # 陽男順行
    else:
        even_tian_gan = [TIAN_GAN[i] for i in range(1,10,2)]
        return tian_gan in even_tian_gan  # 陰女順行

    # 生成大限範圍
def generate_10year_luck(is_male: bool, tian_gan: str, ju: str, ming_palaces:int) -> list[str]:
    start_age = WUXING_JU_MAP[ju] + 2
    direction_forward = is_forward_direction(is_male, tian_gan)

    age_ranges = [f"{start_age + i*10}~{start_age + i*10 + 9}歲" for i in range(12)]
    if not direction_forward:
        age_ranges = list(reversed(age_ranges))
        ming_palaces = (ming_palaces + 1) % 12

    return age_ranges[-ming_palaces:] + age_ranges[:-ming_palaces]

    # 生成小限範圍
def generate_1year_luck(is_male: bool, di_zhi: str) -> list[str]:
    age_ranges = [[(i+1)+j*12 for j in range(7)] for i in range(12)]
    start_ind = XIAO_XIEN_START[di_zhi]
    if not is_male:
        age_ranges = list(reversed(age_ranges))
        start_ind = (start_ind + 1) % 12

    return age_ranges[-start_ind:] + age_ranges[:-start_ind]
