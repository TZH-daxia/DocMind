import re
from functools import lru_cache

# 国际货运托书中常见港口/城市地名白名单（中英文 + 常见旧拼法别名）。
# sfg/mdg 的确定性规则候选必须命中白名单；模型候选未命中时降级为人工复核而不是直接采信。
_PORT_PLACE_NAMES_RAW = (
    # 中国主要空运/海运口岸（中文）
    "上海", "浦东", "虹桥", "深圳", "蛇口", "盐田", "广州", "黄埔", "青岛", "厦门",
    "天津", "新港", "大连", "南京", "杭州", "义乌", "苏州", "太仓", "南通", "连云港",
    "宁波", "北仑", "舟山", "嘉兴", "福州", "泉州", "温州", "武汉", "重庆", "成都",
    "西安", "郑州", "北京", "沈阳", "长春", "哈尔滨", "昆明", "贵阳", "南宁", "海口",
    "三亚", "汕头", "中山", "珠海", "佛山", "东莞", "江门", "湛江", "北海", "秦皇岛",
    "烟台", "日照", "无锡", "常州", "合肥", "南昌", "长沙", "石家庄", "太原", "呼和浩特",
    "兰州", "西宁", "银川", "乌鲁木齐", "拉萨", "香港", "澳门", "台北", "高雄", "基隆", "台中",
    # 中国主要口岸（英文/拼音）
    "SHANGHAI", "PUDONG", "HONGQIAO", "SHENZHEN", "SHEKOU", "YANTIAN", "CHIWAN",
    "GUANGZHOU", "HUANGPU", "QINGDAO", "XIAMEN", "TIANJIN", "XINGANG", "DALIAN",
    "NANJING", "HANGZHOU", "YIWU", "SUZHOU", "TAICANG", "NANTONG", "LIANYUNGANG",
    "NINGBO", "BEILUN", "ZHOUSHAN", "JIAXING", "FUZHOU", "QUANZHOU", "WENZHOU",
    "WUHAN", "CHONGQING", "CHENGDU", "SHUANGLIU", "TIANHE", "XI'AN", "XIAN",
    "ZHENGZHOU", "BEIJING", "PEKING", "CAPITAL", "DAXING", "SHENYANG", "CHANGCHUN",
    "HARBIN", "KUNMING", "GUIYANG", "NANNING", "HAIKOU", "SANYA", "SHANTOU",
    "ZHONGSHAN", "ZHUHAI", "FOSHAN", "DONGGUAN", "JIANGMEN", "ZHANJIANG", "BEIHAI",
    "QINHUANGDAO", "YANTAI", "RIZHAO", "WUXI", "CHANGZHOU", "HEFEI", "NANCHANG",
    "CHANGSHA", "SHIJIAZHUANG", "TAIYUAN", "HOHHOT", "LANZHOU", "XINING",
    "YINCHUAN", "URUMQI", "LHASA", "HONG KONG", "HONGKONG", "MACAU", "MACAO",
    "TAIPEI", "KAOHSIUNG", "KEELUNG", "TAICHUNG",
    # 东北亚
    "BUSAN", "PUSAN", "INCHEON", "INCHON", "PYEONGTAEK", "GWANGYANG", "SEOUL",
    "ULSAN", "TOKYO", "YOKOHAMA", "OSAKA", "KOBE", "NAGOYA", "MOJI", "HAKATA",
    "FUKUOKA", "NIIGATA", "SHIMIZU", "NARITA", "KANSAI",
    # 东南亚 / 南亚
    "SINGAPORE", "PORT KLANG", "KLANG", "PENANG", "JOHOR", "TANJUNG PELEPAS",
    "BANGKOK", "LAEM CHABANG", "LEAM CHABANG", "HO CHI MINH", "SAIGON", "HAI PHONG",
    "HAIPHONG", "DANANG", "MANILA", "BATANGAS", "CEBU", "JAKARTA", "SURABAYA",
    "SEMARANG", "BELAWAN", "MEDAN", "PHNOM PENH", "Sihanoukville", "YANGON",
    "COLOMBO", "CHITTAGONG", "DHAKA", "KARACHI", "LAHORE", "ISLAMABAD", "MUMBAI",
    "BOMBAY", "NHAVA SHEVA", "PIPAVAV", "MUNDRA", "CHENNAI", "MADRAS", "KOLKATA",
    "CALCUTTA", "COCHIN", "TUTICORIN", "KATHMANDU",
    # 中东
    "DUBAI", "JEBEL ALI", "SHARJAH", "ABU DHABI", "MUSCAT", "SOHAR", "BAHRAIN",
    "MANAMA", "DOHA", "HAMAD", "KUWAIT", "JEDDAH", "DAMMAM", "RIYADH", "HAIFA",
    "ASHDOD", "TEL AVIV", "BEIRUT", "LATTAKIA",
    # 欧洲
    "ROTTERDAM", "ANTWERP", "HAMBURG", "BREMEN", "BREMERHAVEN", "FRANKFURT",
    "MUNICH", "MUENCHEN", "DUSSELDORF", "DUESSELDORF", "BERLIN", "LEIPZIG",
    "STUTTGART", "HANNOVER", "COLOGNE", "BONN", "DRESDEN", "NUREMBERG",
    "NUERNBERG", "ESSEN", "DORTMUND", "AMSTERDAM", "SCHIPHOL", "BRUSSELS",
    "LIEGE", "ZEEBRUGGE", "PARIS", "LYON", "MARSEILLE", "FOS", "LE HAVRE",
    "TOULOUSE", "MADRID", "BARCELONA", "VALENCIA", "ZARAGOZA", "BILBAO",
    "ALGECIRAS", "LISBON", "PORTO", "MILAN", "MALPENSA", "ROME", "ROMA",
    "NAPLES", "TURIN", "TORINO", "VENICE", "VERONA", "LA SPEZIA", "GENOA",
    "GENOVA", "LIVORNO", "ZURICH", "GENEVA", "BASEL", "VIENNA", "LINZ", "GRAZ",
    "PRAGUE", "WARSAW", "GDANSK", "GDYNIA", "BUDAPEST", "BUCHAREST", "SOFIA",
    "BELGRADE", "ZAGREB", "COPENHAGEN", "AARHUS", "MALMO", "STOCKHOLM",
    "GOTHENBURG", "OSLO", "HELSINKI", "TALLINN", "RIGA", "VILNIUS", "KLAIPEDA",
    "DUBLIN", "CORK", "LONDON", "HEATHROW", "GATWICK", "MANCHESTER",
    "BIRMINGHAM", "EAST MIDLANDS", "FELIXSTOWE", "SOUTHAMPTON", "THAMESPORT",
    "LIVERPOOL", "IMMINGHAM", "GLASGOW", "EDINBURGH", "NEWCASTLE", "BRISTOL",
    "PIRAEUS", "THESSALONIKI", "IZMIR", "ISTANBUL", "AMBARLI", "MERSIN",
    # 非洲
    "CAIRO", "ALEXANDRIA", "PORT SAID", "DAMIETTA", "TUNIS", "ALGIERS",
    "CASABLANCA", "LAGOS", "APAPA", "TINCAN", "ABIDJAN", "TEMA", "DAKAR",
    "DURBAN", "JOHANNESBURG", "CAPE TOWN", "PORT ELIZABETH", "NAIROBI",
    "MOMBASA", "DAR ES SALAAM", "MAURITIUS", "PORT LOUIS", "REUNION",
    # 北美 / 拉美
    "LOS ANGELES", "LONG BEACH", "OAKLAND", "SAN FRANCISCO", "SEATTLE", "TACOMA",
    "PORTLAND", "NEW YORK", "NEWARK", "NORFOLK", "CHARLESTON", "SAVANNAH",
    "MIAMI", "HOUSTON", "DALLAS", "CHICAGO", "O'HARE", "OHARE", "ATLANTA",
    "DENVER", "DETROIT", "BOSTON", "PHILADELPHIA", "BALTIMORE", "WILMINGTON",
    "MOBILE", "NEW ORLEANS", "LAX", "JFK", "ORD", "ATL", "DFW", "HOU", "IAH",
    "EWR", "TORONTO", "MONTREAL", "VANCOUVER", "CALGARY", "EDMONTON", "WINNIPEG",
    "QUEBEC", "HALIFAX", "MEXICO CITY", "VERACRUZ", "MANZANILLO",
    "LAZARO CARDENAS", "GUADALAJARA", "MONTERREY", "SANTOS", "SAO PAULO",
    "RIO DE JANEIRO", "BUENOS AIRES", "VALPARAISO", "SAN ANTONIO", "CALLAO",
    "LIMA", "GUAYAQUIL", "BOGOTA", "CARTAGENA", "BUENAVENTURA", "COLON",
    "PANAMA", "HAVANA",
    # 大洋洲
    "SYDNEY", "MELBOURNE", "BRISBANE", "ADELAIDE", "PERTH", "FREMANTLE",
    "AUCKLAND", "WELLINGTON", "LYTTELTON", "CHRISTCHURCH",
)

_IATA_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")


def normalize_place_name(value: str) -> str:
    """地名标准化：去收尾空白、统一大写、压缩空白、去掉点号与撇号。"""

    text = value.strip().upper()
    text = text.replace(".", "").replace("'", "").replace("’", "")
    return re.sub(r"\s+", " ", text)


@lru_cache(maxsize=1)
def _known_place_names() -> frozenset[str]:
    return frozenset(
        normalize_place_name(name) for name in _PORT_PLACE_NAMES_RAW
    )


def is_known_port_place(value: object) -> bool:
    """判断候选值是否为已知港口/城市地名或机场三字代码。"""

    if not isinstance(value, str):
        return False
    normalized = normalize_place_name(value)
    if not normalized:
        return False
    if normalized in _known_place_names():
        return True
    # 托书常用机场三字代码（SHA/PVG/FRA 等）直接视为地名
    return bool(_IATA_CODE_PATTERN.fullmatch(normalized))
