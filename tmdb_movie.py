import re
import os
from datetime import datetime
import tmdbsimple as tmdb
import xml.etree.ElementTree as ET
from xml.dom import minidom

LANGUAGE = 'zh-CN'           # TMDb API 请求时使用的语言，设置为中文

class TmdbSimpleNfoGenerator:
    def __init__(self, api_key):
        """
        初始化类，设置TMDb的API Key
        """
        tmdb.API_KEY = api_key

    def parse_title_year(self, input_str):
        """
        从输入字符串中提取电影名和年份。
        格式示例：'功夫 (2004)'
        使用正则匹配，匹配形如“标题 (年份)”的字符串。
        返回 (title, year)，如果没匹配年份，year返回None。
        """
        match = re.match(r"^(.*?)\s*\((\d{4})\)$", input_str)
        if match:
            title = match.group(1).strip()
            year = int(match.group(2))
            return title, year
        else:
            # 如果格式不符合，直接返回整个字符串作为标题，年份None
            return input_str.strip(), None

    def search_movie(self, title, year=None):
        """
        使用TMDb搜索电影，支持传入年份以提高准确率。
        先尝试精确匹配标题+年份，若无匹配则尝试模糊匹配标题中的关键词。
        返回匹配到的第一个电影字典或None。
        """
        search = tmdb.Search()
        response = search.movie(query=title, year=year, language=LANGUAGE)
        results = response.get('results', [])
        title_lower = title.lower()

        # 优先查找标题与年份都匹配的电影
        if year:
            for m in results:
                m_title = m.get('title', '').lower()
                m_release = m.get('release_date', '')
                if m_release.startswith(str(year)) and m_title == title_lower:
                    return m
        # 若无精准匹配，模糊匹配标题中包含关键词的第一个结果
        for m in results:
            if title_lower in m.get('title', '').lower():
                return m
        # 以上都不满足则返回第一个搜索结果（如果有）
        return results[0] if results else None

    def prettify_xml(self, elem):
        """
        将ElementTree元素转换成带缩进和换行的漂亮XML字符串。
        同时将plot、outline、tagline字段的文本包裹成CDATA格式，避免特殊字符干扰解析。
        返回bytes类型的XML数据（utf-8编码）。
        """
        rough_str = ET.tostring(elem, encoding='utf-8')
        reparsed = minidom.parseString(rough_str)
        for tag in ["plot", "outline", "tagline"]:
            elements = reparsed.getElementsByTagName(tag)
            for el in elements:
                if el.firstChild:
                    text_content = el.firstChild.nodeValue
                    el.removeChild(el.firstChild)
                    el.appendChild(reparsed.createCDATASection(text_content))
        return reparsed.toprettyxml(indent="  ", encoding='utf-8')

    def create_actor_element(self, actor):
        """
        根据演员字典，创建一个<actor> XML节点，包含演员名、角色、类型和TMDb ID。
        用于电影演员信息的NFO输出。
        """
        actor_el = ET.Element("actor")
        ET.SubElement(actor_el, "name").text = actor.get("name", "")
        ET.SubElement(actor_el, "role").text = actor.get("character", "")
        ET.SubElement(actor_el, "type").text = "Actor"
        ET.SubElement(actor_el, "tmdbid").text = str(actor.get("id", ""))
        return actor_el

    def movie_nfo(self, input_name):
        """
        主函数，传入电影名称（可带年份），生成对应的电影nfo文件。
        文件生成在以“电影名 (年份)”命名的文件夹内。
        """
        # 解析输入字符串，拆出电影名和年份
        title, year = self.parse_title_year(input_name)
        print(f"解析到电影名: {title}，年份: {year}")

        # 使用TMDb搜索电影信息
        movie = self.search_movie(title, year)
        if not movie:
            print("未找到匹配电影")
            return

        # 根据电影名和年份生成文件夹名，过滤非法字符
        folder_name = f"{title} ({year})" if year else title
        folder_name = re.sub(r'[\\/:*?"<>|]', '', folder_name).strip()
        # 创建文件夹，exist_ok=True表示已存在时不报错
        os.makedirs(folder_name, exist_ok=True)

        # 生成安全的文件名，同样过滤非法字符
        safe_title = re.sub(r'[\\/:*?"<>|]', '', title).strip()
        filename = f"{safe_title} ({year}).nfo" if year else f"{safe_title}.nfo"

        # 调用TMDb接口获取电影详细信息、演员表、视频信息
        movie_id = movie['id']
        m = tmdb.Movies(movie_id)
        movie_details = m.info(language=LANGUAGE)
        credits = m.credits(language=LANGUAGE)
        videos = m.videos(language=LANGUAGE)

        # 创建根节点<movie>
        root = ET.Element("movie")

        # 设置主要文本字段，简介(plot)、概述(outline)、标语(tagline)，注意后面转成CDATA
        ET.SubElement(root, "plot").text = movie_details.get("overview", "") or ""
        ET.SubElement(root, "outline").text = movie_details.get("tagline", "") or ""
        ET.SubElement(root, "tagline").text = movie_details.get("tagline", "") or ""

        # 其他基础字段
        ET.SubElement(root, "lockdata").text = "false"
        ET.SubElement(root, "dateadded").text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ET.SubElement(root, "title").text = movie_details.get("title", "")
        ET.SubElement(root, "originaltitle").text = movie_details.get("original_title", "")

        # 添加最多30个演员节点
        cast = credits.get("cast", [])[:30]
        for actor in cast:
            actor_el = self.create_actor_element(actor)
            root.append(actor_el)

        # 添加导演信息，取职务为Director的成员
        crew = credits.get("crew", [])
        for member in crew:
            if member.get("job") == "Director":
                director_el = ET.SubElement(root, "director", tmdbid=str(member.get("id", "")))
                director_el.text = member.get("name", "")

        # 查找YouTube类型为Trailer的视频，生成插件调用链接
        trailer_url = ""
        for v in videos.get("results", []):
            if v.get("site") == "YouTube" and v.get("type") == "Trailer":
                trailer_url = f"plugin://plugin.video.youtube/play/?video_id={v.get('key')}"
                break
        ET.SubElement(root, "trailer").text = trailer_url

        # 添加评分、年份、排序标题、分级、IMDb ID、TMDb ID、首映日期、时长等信息
        ET.SubElement(root, "rating").text = str(round(movie_details.get("vote_average", 0), 1))
        ET.SubElement(root, "year").text = movie_details.get("release_date", "")[:4] if movie_details.get("release_date") else ""
        ET.SubElement(root, "sorttitle").text = movie_details.get("title", "")
        ET.SubElement(root, "mpaa").text = "R" if movie_details.get("adult") else "PG-13"
        ET.SubElement(root, "imdbid").text = movie_details.get("imdb_id", "") or ""
        ET.SubElement(root, "tmdbid").text = str(movie_details.get("id", ""))
        ET.SubElement(root, "premiered").text = movie_details.get("release_date", "") or ""
        ET.SubElement(root, "releasedate").text = movie_details.get("release_date", "") or ""
        ET.SubElement(root, "runtime").text = str(movie_details.get("runtime", 0))

        # 添加制片国家
        for c in movie_details.get("production_countries", []):
            ET.SubElement(root, "country").text = c.get("name", "")

        # 添加影片类型（分类）
        for g in movie_details.get("genres", []):
            ET.SubElement(root, "genre").text = g.get("name", "")

        # 添加制片公司
        for p in movie_details.get("production_companies", []):
            ET.SubElement(root, "studio").text = p.get("name", "")

        # 添加唯一ID（TMDb和IMDb）
        ET.SubElement(root, "uniqueid", type="tmdb").text = str(movie_details.get("id", ""))
        ET.SubElement(root, "uniqueid", type="imdb").text = movie_details.get("imdb_id", "") or ""
        ET.SubElement(root, "id").text = movie_details.get("imdb_id", "") or ""

        # 文件信息示例，填充视频流相关字段（可根据实际情况修改）
        # fileinfo = ET.SubElement(root, "fileinfo")
        # streamdetails = ET.SubElement(fileinfo, "streamdetails")
        # video = ET.SubElement(streamdetails, "video")
        # ET.SubElement(video, "codec").text = "hevc"
        # ET.SubElement(video, "micodec").text = "hevc"
        # ET.SubElement(video, "bitrate").text = "1658118"
        # ET.SubElement(video, "width").text = "3840"
        # ET.SubElement(video, "height").text = "1600"
        # ET.SubElement(video, "aspect").text = "2.40:1"
        # ET.SubElement(video, "aspectratio").text = "2.40:1"
        # ET.SubElement(video, "framerate").text = "23.976"
        # ET.SubElement(video, "language").text = "und"
        # ET.SubElement(video, "scantype").text = "progressive"
        # ET.SubElement(video, "default").text = "True"
        # ET.SubElement(video, "forced").text = "False"
        # ET.SubElement(video, "duration").text = str(movie_details.get("runtime", 0))
        # ET.SubElement(video, "durationinseconds").text = str((movie_details.get("runtime", 0)) * 60)

        # 生成漂亮格式的XML字符串
        xml_data = self.prettify_xml(root)

        # 组合完整的文件路径，放到对应的电影文件夹中
        file_path = os.path.join(folder_name, filename)

        # 以二进制写方式写入文件，写入XML声明头和内容
        with open(file_path, "wb") as f:
            f.write(b'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n')
            f.write(xml_data)

        print(f"NFO文件已生成: {file_path}")


if __name__ == "__main__":
    # 示例入口，提示输入电影名（带年份）
    movie_name = input("请输入电影名称 (年份)，如：功夫 (2004): ").strip()
    # 初始化生成器，传入你的TMDb API Key
    generator = TmdbSimpleNfoGenerator("TMDb API Key")
    # 生成对应的nfo文件
    generator.movie_nfo(movie_name)
