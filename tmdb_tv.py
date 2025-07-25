import os
import re
import configparser
import xml.etree.ElementTree as ET
from xml.dom import minidom
from tmdbv3api import TMDb, TV, Search, Season, Episode
import requests

# 配置文件 config.ini
"""
[tmdb]
# 你的 TMDb API Key，必须填写，从 https://www.themoviedb.org/ 申请
api_key = 你的TMDbAPIKey
# 语言设置，默认为中文简体，支持 TMDb 支持的语言代码，例如 zh-CN、en-US
language = zh-CN
# 是否生成 tvshow.nfo 文件，true 生成，false 不生成
generate_tvshow_nfo = true
# 是否生成 season.nfo 文件，true 生成，false 不生成
generate_season_nfo = true

[proxy]
# 添加代理
http = http://127.0.0.1:7890
https = http://127.0.0.1:7890
"""


class TVShowNfoGenerator:
    """
    根据输入剧集名 (年份) SxxExx 格式，生成 Emby 需要的 NFO 文件：
    包括 tvshow.nfo、season.nfo 和 SxxExx.nfo
    """

    def __init__(self, config_file='config.ini'):
        """
        初始化配置和 TMDb API
        """
        # 加载配置文件
        self.config = configparser.ConfigParser()
        self.config.read(config_file, encoding='utf-8')

        # 从配置获取 API Key 和语言，是否生成 tvshow.nfo 和 season.nfo
        self.api_key = self.config['tmdb']['api_key']
        self.language = self.config.get('tmdb', 'language', fallback='zh-CN')
        self.tvshow_nfo = self.config.getboolean('tmdb', 'generate_tvshow_nfo', fallback=True)
        self.season_nfo = self.config.getboolean('tmdb', 'generate_season_nfo', fallback=True)

        # 读取代理配置
        self.proxies = None
        if 'proxy' in self.config:
            self.proxies = {
                'http': self.config['proxy'].get('http', None),
                'https': self.config['proxy'].get('https', None)
            }
            self.proxies = {k: v for k, v in self.proxies.items() if v}
            # 设置环境变量，供 tmdbv3api 使用
            if 'http' in self.proxies:
                os.environ['HTTP_PROXY'] = self.proxies['http']
            if 'https' in self.proxies:
                os.environ['HTTPS_PROXY'] = self.proxies['https']

        # 初始化 TMDb 和相关 API 实例
        self.tmdb = TMDb()
        self.tmdb.api_key = self.api_key
        self.tmdb.language = self.language
        self.search = Search()
        self.tv_api = TV()

    def parse_tv_input(self, input_str):
        """
        解析输入字符串，格式：剧名 (年份) SxxExx
        :param input_str: 输入字符串
        :return: 字典{title, year, season, episode} 或 None
        """
        pattern = r"^(.*?)\s*\((\d{4})\)\s*[sS](\d{2})[eE](\d{2})$"
        match = re.match(pattern, input_str.strip())
        if match:
            return {
                'title': match.group(1).strip(),
                'year': int(match.group(2)),
                'season': int(match.group(3)),
                'episode': int(match.group(4))
            }
        return None

    def search_tv_show(self, title, year):
        """
        使用 TMDb 搜索剧集，匹配名称和首播年份
        :param title: 剧名
        :param year: 年份
        :return: tmdbv3api TV 对象或 None
        """
        results = self.search.tv_shows(title)
        title_lower = title.lower()
        for tv in results:
            # 名称或原名匹配（忽略大小写），且首播年份匹配
            if (tv.name.lower() == title_lower or tv.original_name.lower() == title_lower) \
               and tv.first_air_date and tv.first_air_date.startswith(str(year)):
                return tv
        return None

    def create_xml_file(self, root, path):
        """
        将 ElementTree XML 元素写入美化后的 XML 文件
        :param root: XML 根元素
        :param path: 文件保存路径
        """
        xml_str = ET.tostring(root, encoding='utf-8')
        pretty_xml = minidom.parseString(xml_str).toprettyxml(indent="  ", encoding='utf-8')
        with open(path, "wb") as f:
            f.write(pretty_xml)

    def get_tv_details_dict(self, tv_id):
        """
        通过 TMDb API 获取电视剧详细信息，返回字典格式
        :param tv_id: TMDb 电视剧 ID
        :return: 电视剧详细信息 dict
        """
        url = f"https://api.themoviedb.org/3/tv/{tv_id}"
        params = {'api_key': self.api_key, 'language': self.language}
        try:
            resp = requests.get(url, params=params, timeout=10, proxies=self.proxies)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"获取电视剧详情失败: {e}")
            return None

    def get_aggregate_credits(self, tv_id):
        """
        获取电视剧聚合演职员信息
        :param tv_id: TMDb 电视剧 ID
        :return: 演职员信息 dict
        """
        url = f"https://api.themoviedb.org/3/tv/{tv_id}/aggregate_credits"
        params = {'api_key': self.api_key, 'language': self.language}
        try:
            resp = requests.get(url, params=params, timeout=10, proxies=self.proxies)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"获取演职员信息失败: {e}")
            return {}

    def generate_tvshow_nfo(self, tv_details, output_folder):
        """
        生成 tvshow.nfo
        :param tv_details: 电视剧详情字典
        :param output_folder: 输出文件夹
        """
        if not tv_details:
            print("tv_details 为空，跳过生成 tvshow.nfo")
            return

        tv_id = tv_details.get('id')
        credits = self.get_aggregate_credits(tv_id)

        root = ET.Element("tvshow")
        ET.SubElement(root, "title").text = tv_details.get('name', '')
        ET.SubElement(root, "originaltitle").text = tv_details.get('original_name', '')
        ET.SubElement(root, "plot").text = tv_details.get('overview', '')
        first_air_date = tv_details.get('first_air_date', '')
        ET.SubElement(root, "year").text = first_air_date[:4] if first_air_date else ''
        ET.SubElement(root, "id").text = str(tv_id)

        for actor in credits.get('cast', []):
            name = actor.get('name', '')
            role = ''
            roles = actor.get('roles', [])
            if roles and isinstance(roles, list):
                first_role = roles[0]
                if isinstance(first_role, dict):
                    role = first_role.get('character', '')
            if not role:
                role = actor.get('character', '')

            actor_el = ET.SubElement(root, "actor")
            ET.SubElement(actor_el, "name").text = name
            ET.SubElement(actor_el, "role").text = role or ""

        os.makedirs(output_folder, exist_ok=True)
        nfo_path = os.path.join(output_folder, "tvshow.nfo")
        self.create_xml_file(root, nfo_path)

    def generate_season_nfo(self, season_data, folder_path):
        """
        生成 season.nfo
        :param season_data: tmdbv3api Season 对象
        :param folder_path: 文件夹路径
        """
        if not season_data:
            print("season_data 为空，跳过生成 season.nfo")
            return

        root = ET.Element("season")
        ET.SubElement(root, "seasonnumber").text = str(season_data.season_number)
        ET.SubElement(root, "title").text = season_data.name or f"Season {season_data.season_number}"
        ET.SubElement(root, "plot").text = season_data.overview or ""
        nfo_path = os.path.join(folder_path, "season.nfo")
        self.create_xml_file(root, nfo_path)

    def sanitize_filename(self, s):
        """
        去除非法文件名字符，避免 Windows 文件名错误
        :param s: 原字符串
        :return: 过滤后的字符串
        """
        return re.sub(r'[\\/:*?"<>|]', '', s)

    def generate_episode_nfo(self, episode_data, folder_path, season, episode, tv_name):
        """
        生成 SxxExx.nfo 文件
        :param episode_data: tmdbv3api Episode 对象
        :param folder_path: 文件夹路径
        :param season: 季数
        :param episode: 集数
        :param tv_name: 电视剧名称（用于文件名安全处理）
        """
        if not episode_data:
            print("episode_data 为空，跳过生成集数 NFO")
            return

        root = ET.Element("episodedetails")
        ET.SubElement(root, "title").text = episode_data.name
        ET.SubElement(root, "season").text = str(season)
        ET.SubElement(root, "episode").text = str(episode)
        ET.SubElement(root, "aired").text = episode_data.air_date or ""
        ET.SubElement(root, "plot").text = episode_data.overview or ""
        ET.SubElement(root, "id").text = str(episode_data.id)

        # 文件名格式 S01E01.nfo（Emby 标准）
        filename = f"S{season:02d}E{episode:02d}.nfo"
        nfo_path = os.path.join(folder_path, filename)
        self.create_xml_file(root, nfo_path)

    def run(self, input_str=None):
        """
        主流程入口，接收输入字符串，生成 NFO 文件
        """
        # 循环直到输入格式正确
        while True:
            if not input_str:
                input_str = input("请输入 剧名 (年份) SxxExx（例如：权力的游戏 (2011) S01E01）: ").strip()
            parsed = self.parse_tv_input(input_str)
            if parsed:
                break
            else:
                print("输入格式错误，请重新输入。")
                input_str = None

        # 搜索电视剧
        tv_show = self.search_tv_show(parsed['title'], parsed['year'])
        if not tv_show:
            print(f"❌ 未找到剧集: {parsed['title']} ({parsed['year']})")
            return

        # 获取电视剧详细信息（字典）
        tv_details = self.get_tv_details_dict(tv_show.id)
        if not tv_details:
            print("❌ 无法获取电视剧详情，程序退出。")
            return

        # 获取季信息
        try:
            season_data = Season().details(tv_show.id, parsed['season'])
        except Exception as e:
            print(f"获取季信息失败: {e}")
            season_data = None

        # 获取集信息
        try:
            episode_data = Episode().details(tv_show.id, parsed['season'], parsed['episode'])
        except Exception as e:
            print(f"获取集信息失败: {e}")
            episode_data = None

        # 创建根目录和季目录，目录名做安全处理
        safe_tv_name = self.sanitize_filename(tv_details.get('name', parsed['title']))
        root_folder = os.path.join(os.getcwd(), f"{safe_tv_name} ({parsed['year']})")
        os.makedirs(root_folder, exist_ok=True)

        season_folder_name = f"S{parsed['season']:02d}"
        season_folder = os.path.join(root_folder, season_folder_name)
        os.makedirs(season_folder, exist_ok=True)

        # 生成 tvshow.nfo
        if self.tvshow_nfo:
            self.generate_tvshow_nfo(tv_details, root_folder)

        # 生成 season.nfo
        if self.season_nfo:
            self.generate_season_nfo(season_data, season_folder)

        # 生成 episode.nfo
        self.generate_episode_nfo(episode_data, season_folder, parsed['season'], parsed['episode'], safe_tv_name)

        print(f"✅ NFO 文件生成完成，目录: {root_folder}")

if __name__ == "__main__":
    generator = TVShowNfoGenerator()
    generator.run()
