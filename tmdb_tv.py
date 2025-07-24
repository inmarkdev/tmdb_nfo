import os
import re
import xml.etree.ElementTree as ET
from xml.dom import minidom
import tmdbsimple as tmdb

class TVEpisodeNfoGenerator:
    def __init__(self, api_key):
        tmdb.API_KEY = api_key

    def parse_input(self, user_input):
        """
        解析用户输入格式：
        电视剧名 (年份) SXXEXX
        返回字典包含：
        title, year, season, episode
        """
        pattern_ep = r"^(.*?)\s*\((\d{4})\)\s+S(\d{1,2})E(\d{1,2})$"
        m = re.match(pattern_ep, user_input, re.I)
        if m:
            return {
                "title": m.group(1).strip(),
                "year": int(m.group(2)),
                "season": int(m.group(3)),
                "episode": int(m.group(4))
            }
        return None

    def search_tv(self, title, year=None):
        search = tmdb.Search()
        response = search.tv(query=title)
        results = response.get('results', [])
        if not results:
            return None
        for show in results:
            if year and show.get('first_air_date'):
                if show['name'].lower() == title.lower() and show['first_air_date'].startswith(str(year)):
                    return show
        return results[0]

    def prettify_xml(self, elem):
        """
        格式化xml，plot和outline字段加CDATA包装
        """
        rough_str = ET.tostring(elem, encoding='utf-8')
        reparsed = minidom.parseString(rough_str)

        for tag in ["plot", "outline"]:
            elements = reparsed.getElementsByTagName(tag)
            for el in elements:
                if el.firstChild:
                    text_content = el.firstChild.nodeValue
                    el.removeChild(el.firstChild)
                    el.appendChild(reparsed.createCDATASection(text_content))

        return reparsed.toprettyxml(indent="  ", encoding='utf-8')

    def generate_tv_episode_nfo(self, tv_id, season_num, episode_num, show_name, year=None):
        """
        根据剧集信息生成单集nfo文件
        路径格式：电视剧名 (年份)/Sxx/电视剧名 (年份).SxxExx.nfo
        """
        episode = tmdb.TV_Episodes(tv_id, season_num, episode_num)
        ep_info = episode.info(language='zh-CN')

        root = ET.Element("episodedetails")
        ET.SubElement(root, "title").text = ep_info.get("name") or ""
        ET.SubElement(root, "showtitle").text = show_name
        ET.SubElement(root, "season").text = str(season_num)
        ET.SubElement(root, "episode").text = str(episode_num)
        ET.SubElement(root, "aired").text = ep_info.get("air_date") or ""
        ET.SubElement(root, "plot").text = ep_info.get("overview") or ""
        ET.SubElement(root, "director").text = ", ".join([d['name'] for d in ep_info.get("crew", []) if d.get("job") == "Director"])
        ET.SubElement(root, "rating").text = str(round(ep_info.get("vote_average", 0), 1))

        # runtime处理，若无单集runtime则用季信息
        runtime = ep_info.get("runtime")
        if runtime is None:
            tv_season = tmdb.TV_Seasons(tv_id, season_num)
            tv_season.language = 'zh-CN'
            season_info = tv_season.info()
            runtime_list = season_info.get("episode_run_time", [])
            runtime = runtime_list[0] if runtime_list else 0
        ET.SubElement(root, "runtime").text = str(runtime or 0)

        folder_name = f"{show_name} ({year})" if year else show_name
        safe_folder_name = re.sub(r'[\\/:*?"<>|]', '', folder_name)
        dir_path = os.path.join(safe_folder_name, f"S{season_num:02d}")
        os.makedirs(dir_path, exist_ok=True)

        filename = os.path.join(show_name, f"{safe_folder_name}.S{season_num:02d}E{episode_num:02d}.nfo")
        xml_data = self.prettify_xml(root)
        with open(filename, "wb") as f:
            f.write(b'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n')
            f.write(xml_data)
        print(f"已生成剧集NFO: {filename}")

    def run(self, user_input):
        parsed = self.parse_input(user_input)
        if not parsed:
            print("输入格式错误，请输入“电视剧名 (年份) SxxExx”")
            return

        tv_show = self.search_tv(parsed['title'], parsed['year'])
        if not tv_show:
            print("未找到匹配的电视剧")
            return

        self.generate_tv_episode_nfo(tv_show['id'], parsed['season'], parsed['episode'], parsed['title'], parsed['year'])


if __name__ == "__main__":
    user_input = input("请输入电视剧名称 (年份) SxxExx: ").strip()
    generator = TVEpisodeNfoGenerator("")
    generator.run(user_input)
