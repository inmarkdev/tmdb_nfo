import os
import re
import configparser
import xml.etree.ElementTree as ET
from xml.dom import minidom
from tmdbv3api import TMDb, Movie, Search

# 配置文件 config.ini
"""
[tmdb]
# 你的 TMDb API Key，必须填写，从 https://www.themoviedb.org/ 申请
api_key = 你的TMDbAPIKey
# 语言设置，默认为中文简体，支持 TMDb 支持的语言代码，例如 zh-CN、en-US
language = zh-CN

[proxy]
# 代理
http = http://127.0.0.1:7890
https = http://127.0.0.1:7890
"""

class MovieNfoGenerator:
    """
    电影 NFO 信息生成器
    使用 TMDb 数据，根据“电影名 (年份)”格式生成 Emby 兼容的 movie.nfo 文件。
    """

    def __init__(self, config_file='config.ini'):
        """
        初始化 TMDb 连接与配置读取
        :param config_file: 配置文件路径（默认为 config.ini）
        """
        self.config = configparser.ConfigParser()
        self.config.read(config_file, encoding='utf-8')

        # 读取 TMDb API Key 和语言设置
        self.api_key = self.config['tmdb']['api_key']
        self.language = self.config.get('tmdb', 'language', fallback='zh-CN')

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

        # 初始化 TMDb API
        self.tmdb = TMDb()
        self.tmdb.api_key = self.api_key
        self.tmdb.language = self.language

        self.search = Search()
        self.movie_api = Movie()

    def parse_movie(self, movie_year):
        """
        解析“电影名 (年份)”格式为 {'title': ..., 'year': ...}
        :param movie_year: str，例如“功夫 (2004)”
        :return: dict 或 None
        """
        m = re.match(r"^(.*?)\s*\((\d{4})\)$", movie_year)
        if m:
            return {
                "title": m.group(1).strip(),
                "year": int(m.group(2))
            }
        return None

    def search_movie(self, title, year):
        """
        搜索电影，要求 title 精确匹配，year 用于过滤
        :param title: str
        :param year: int
        :return: 匹配的 movie 对象或 None
        """
        results = self.search.movies(title, year=year)
        for m in results:
            # 精确匹配标题，年份前缀匹配
            if m.title == title and (not year or m.release_date.startswith(str(year))):
                return m
        return None

    def get_directors(self, credits):
        """
        获取导演姓名列表
        :param credits: 通过 movie_api.credits() 获取
        :return: list[str]
        """
        return [p.name for p in credits.crew if p.job == "Director"]

    def get_actors(self, credits):
        """
        获取演员列表（包含角色名、排序）
        :param credits: 通过 movie_api.credits() 获取
        :return: list[actor对象]
        """
        return credits.cast

    def generate_nfo(self, movie_data, credits):
        """
        根据电影详细信息与演职员表，生成符合 Emby 要求的 movie.nfo 文件。
        :param movie_data: Movie().details(id)
        :param credits: Movie().credits(id)
        """
        # 构造 XML 树结构
        root = ET.Element("movie")
        ET.SubElement(root, "title").text = movie_data.title
        ET.SubElement(root, "originaltitle").text = movie_data.original_title
        ET.SubElement(root, "sorttitle").text = movie_data.title
        ET.SubElement(root, "year").text = movie_data.release_date[:4]
        ET.SubElement(root, "releasedate").text = movie_data.release_date
        ET.SubElement(root, "plot").text = movie_data.overview
        ET.SubElement(root, "runtime").text = str(movie_data.runtime or "")
        ET.SubElement(root, "rating").text = str(movie_data.vote_average)
        ET.SubElement(root, "id").text = str(movie_data.id)

        # 添加导演节点
        for director in self.get_directors(credits):
            ET.SubElement(root, "director").text = director

        # 添加演员节点
        for actor in self.get_actors(credits):
            actor_el = ET.SubElement(root, "actor")
            ET.SubElement(actor_el, "name").text = actor.name
            ET.SubElement(actor_el, "role").text = actor.character
            ET.SubElement(actor_el, "order").text = str(actor.order)

        # 构造目录名：电影名 (年份)
        title_year = f"{movie_data.title} ({movie_data.release_date[:4]})"
        folder_path = os.path.join(os.getcwd(), title_year)
        os.makedirs(folder_path, exist_ok=True)

        # NFO 文件路径
        nfo_path = os.path.join(folder_path, f"{title_year}.nfo")

        # 美化 XML，写入文件
        xml_str = ET.tostring(root, encoding='utf-8')
        pretty_xml = minidom.parseString(xml_str).toprettyxml(indent="  ", encoding='utf-8')
        with open(nfo_path, "wb") as f:
            f.write(pretty_xml)

        print(f"✅ 成功生成: {nfo_path}")

    def run(self, movie_year_str=None):
        """
        主入口方法，输入一个“电影名 (年份)”字符串，生成对应的 movie.nfo 文件。
        :param movie_year_str: 可选输入，如无则提示用户输入
        """
        if not movie_year_str:
            movie_year_str = input("请输入电影名称 (年份)，如：功夫 (2004): ").strip()

        # 解析并验证输入格式
        parsed = self.parse_movie(movie_year_str)
        while not parsed:
            movie_year_str = input("格式错误，请输入电影名称 (年份)，如：功夫 (2004): ").strip()
            parsed = self.parse_movie(movie_year_str)

        # 执行搜索
        movie_show = self.search_movie(parsed['title'], parsed['year'])
        if not movie_show:
            print("❌ 未找到匹配电影")
            return

        # 获取详情并生成 NFO
        details = self.movie_api.details(movie_show.id)
        credits = self.movie_api.credits(movie_show.id)
        self.generate_nfo(details, credits)

if __name__ == "__main__":
    generator = MovieNfoGenerator()
    generator.run()  # 手动输入
