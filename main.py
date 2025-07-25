import os
import re
import configparser
import xml.etree.ElementTree as ET
from xml.dom import minidom
import asyncio
import aiohttp
import aiofiles
from tqdm import tqdm
import logging
import time
from typing import Optional, List

# 设置日志输出到控制台和文件
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("tmdb_nfo_generator.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger()

def log_with_tqdm(message: str, level: str = "info"):
    """
    统一日志输出函数。
    使用 tqdm.write 保证控制台日志与进度条不冲突。
    同时写入日志文件。

    :param message: 日志内容字符串
    :param level: 日志级别，支持 'info', 'warning', 'error', 'debug'
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} - {level.upper()} - {message}"

    tqdm.write(line)

    if level == "info":
        logger.info(message)
    elif level == "warning":
        logger.warning(message)
    elif level == "error":
        logger.error(message)
    elif level == "debug":
        logger.debug(message)
    else:
        logger.info(message)

TMDB_BASE_URL = "https://api.themoviedb.org/3"

TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/original"

class BaseNfoGenerator:
    def __init__(self, config_file='config.ini'):
        self.config = configparser.ConfigParser()
        self.config.read(config_file, encoding='utf-8')

        self.api_key = self.config['tmdb']['api_key']
        self.language = self.config.get('tmdb', 'language', fallback='zh-CN')
        self.video_exts_list = [e.strip().lower() for e in self.config.get('tmdb', 'video_exts', fallback='strm,mp4,mkv,flv,avi,mov,wmv,ts,m2ts').split(',') if e.strip()]
        self.proxy = self.config['proxy'].get('https', None) if 'proxy' in self.config else None


class TvNfoGenerator(BaseNfoGenerator):
    def __init__(self, config_file='config.ini'):
        super().__init__(config_file)
        self.tv_dirs = [d.strip() for d in self.config.get('tmdb', 'tv_dir', fallback='media/电视节目').split(',') if d.strip()]
        self.failed_tv: List[str] = []
        self.tvshow_generated_dirs = set()  # 避免重复生成 tvshow.nfo
        self.season_generated_dirs = set()  # 避免重复生成 season.nfo

    def parse_tv(self, filename: str) -> Optional[tuple[str, int, int]]:
        """
        支持解析剧集文件名格式：
        - 剧名.S01E02.ext
        - 剧名 - S01E02.ext
        返回 (剧名, season, episode) 或 None
        """
        name, _ = os.path.splitext(filename)
        # 匹配示例：
        # 1. 剧名.S01E02
        # 2. 剧名 - S01E02
        pattern = r'(.+?)[. ]?[-]? ?[Ss](\d{1,2})[Ee](\d{1,2})$'
        m = re.match(pattern, name, re.IGNORECASE)
        if m:
            show_name = m.group(1).replace('.', ' ').strip()
            season = int(m.group(2))
            episode = int(m.group(3))
            log_with_tqdm(f"解析成功: {filename} => {show_name} S{season}E{episode}", "debug")
            return show_name, season, episode

        log_with_tqdm(f"无法解析剧集文件名格式: {filename}", "warning")
        self.failed_tv.append(filename)
        return None

    async def search_tv(self, session: aiohttp.ClientSession, show_name: str) -> Optional[dict]:
        params = {
            "api_key": self.api_key,
            "query": show_name,
            "language": self.language
        }
        async with session.get(f"{TMDB_BASE_URL}/search/tv", params=params, proxy=self.proxy, ssl=False) as resp:
            data = await resp.json()
            results = data.get("results", [])
            if results:
                # 取第一个结果作为匹配（可按需改进）
                return results[0]
        log_with_tqdm(f"未找到 TMDb 电视剧匹配: {show_name}", "warning")
        self.failed_tv.append(show_name)
        return None

    async def get_tv_credits(self, session: aiohttp.ClientSession, tv_id: int) -> dict:
        params = {"api_key": self.api_key, "language": self.language}
        async with session.get(f"{TMDB_BASE_URL}/tv/{tv_id}/credits", params=params, proxy=self.proxy, ssl=False) as resp:
            return await resp.json()

    async def download_image(self, session: aiohttp.ClientSession, url: str, save_path: str):
        if not url:
            log_with_tqdm(f"无效的图片URL，跳过下载: {save_path}", "warning")
            return
        if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
            log_with_tqdm(f"图片已存在，跳过下载: {save_path}", "info")
            return
        try:
            async with session.get(url, proxy=self.proxy, ssl=False, timeout=15) as resp:
                if resp.status == 200:
                    f = await aiofiles.open(save_path, mode='wb')
                    await f.write(await resp.read())
                    await f.close()
                    log_with_tqdm(f"✅ 成功下载图片: {save_path}", "info")
                else:
                    log_with_tqdm(f"❌ 下载图片失败，状态码: {resp.status}，路径: {save_path}", "warning")
        except Exception as e:
            log_with_tqdm(f"❌ 下载图片异常: {e}，路径: {save_path}", "error")

    async def download_tvshow_images(self, session: aiohttp.ClientSession, tv_data: dict, folder: str):
        base_url = TMDB_IMAGE_BASE_URL
        poster_path = tv_data.get("poster_path")
        backdrop_path = tv_data.get("backdrop_path")

        if poster_path:
            poster_url = base_url + poster_path
            poster_save_path = os.path.join(folder, "poster.jpg")
            await self.download_image(session, poster_url, poster_save_path)
        else:
            log_with_tqdm("tvshow 无 poster_path，跳过 poster.jpg 下载", "warning")

        if backdrop_path:
            fanart_url = base_url + backdrop_path
            fanart_save_path = os.path.join(folder, "fanart.jpg")
            await self.download_image(session, fanart_url, fanart_save_path)
        else:
            log_with_tqdm("tvshow 无 backdrop_path，跳过 fanart.jpg 下载", "warning")

    async def download_season_images(self, session: aiohttp.ClientSession, tv_id: int, season_number: int, folder: str):
        params = {"api_key": self.api_key, "language": self.language}
        async with session.get(f"{TMDB_BASE_URL}/tv/{tv_id}/season/{season_number}", params=params, proxy=self.proxy, ssl=False) as resp:
            season_data = await resp.json()

        base_url = TMDB_IMAGE_BASE_URL
        poster_path = season_data.get("poster_path")
        backdrop_path = season_data.get("backdrop_path")

        if poster_path:
            poster_url = base_url + poster_path
            poster_save_path = os.path.join(folder, "season-poster.jpg")
            await self.download_image(session, poster_url, poster_save_path)
        else:
            log_with_tqdm(f"Season {season_number} 无 poster_path，跳过 season-poster.jpg 下载", "warning")

        if backdrop_path:
            fanart_url = base_url + backdrop_path
            fanart_save_path = os.path.join(folder, "season-fanart.jpg")
            await self.download_image(session, fanart_url, fanart_save_path)
        else:
            log_with_tqdm(f"Season {season_number} 无 backdrop_path，跳过 season-fanart.jpg 下载", "warning")

    async def download_episode_image(self, session: aiohttp.ClientSession, tv_id: int, season: int, episode: int,
                                     folder: str, episode_filepath: str):
        params = {"api_key": self.api_key, "language": self.language}
        async with session.get(f"{TMDB_BASE_URL}/tv/{tv_id}/season/{season}/episode/{episode}", params=params,
                               proxy=self.proxy, ssl=False) as resp:
            ep_data = await resp.json()

        base_url = TMDB_IMAGE_BASE_URL
        still_path = ep_data.get("still_path")

        if still_path:
            still_url = base_url + still_path
            # 用剧集文件名（无扩展名）作为图片名
            base_filename = os.path.splitext(os.path.basename(episode_filepath))[0]
            still_save_path = os.path.join(folder, f"{base_filename}.jpg")
            await self.download_image(session, still_url, still_save_path)
        else:
            log_with_tqdm(f"S{season}E{episode} 无 still_path，跳过剧集剧照下载", "warning")

    async def generate_tvshow_nfo(self, session: aiohttp.ClientSession, tv_data: dict, credits: dict, folder: str):
        nfo_path = os.path.join(folder, "tvshow.nfo")
        if os.path.exists(nfo_path):
            log_with_tqdm(f"tvshow.nfo 已存在，跳过: {nfo_path}", "info")
            return
        root = ET.Element("tvshow")
        ET.SubElement(root, "title").text = tv_data["name"]
        ET.SubElement(root, "originaltitle").text = tv_data.get("original_name", tv_data["name"])
        ET.SubElement(root, "sorttitle").text = tv_data["name"]
        ET.SubElement(root, "year").text = tv_data.get("first_air_date", "")[:4]
        ET.SubElement(root, "plot").text = tv_data.get("overview", "")
        ET.SubElement(root, "id").text = str(tv_data["id"])

        creators = tv_data.get("created_by", [])
        for c in creators:
            ET.SubElement(root, "director").text = c.get("name", "")

        for actor in credits.get('cast', []):
            actor_el = ET.SubElement(root, "actor")
            ET.SubElement(actor_el, "name").text = actor.get('name', '')
            ET.SubElement(actor_el, "role").text = actor.get('character', '')
            ET.SubElement(actor_el, "order").text = str(actor.get('order', ''))

        xml_str = ET.tostring(root, encoding='utf-8')
        pretty_xml = minidom.parseString(xml_str).toprettyxml(indent="  ", encoding='utf-8')
        async with aiofiles.open(nfo_path, "wb") as f:
            await f.write(pretty_xml)
        log_with_tqdm(f"成功生成 TVSHOW NFO: {nfo_path}", "info")

        # 下载电视剧海报和剧照
        log_with_tqdm("【调试】即将下载电视剧图片...", "info")
        await self.download_tvshow_images(session, tv_data, folder)
        log_with_tqdm("【调试】下载电视剧图片完成", "info")

    async def generate_season_nfo(self, session: aiohttp.ClientSession, tv_id: int, season_number: int, folder: str):
        nfo_path = os.path.join(folder, "season.nfo")
        if os.path.exists(nfo_path):
            log_with_tqdm(f"season.nfo 已存在，跳过: {nfo_path}", "info")
            return
        params = {"api_key": self.api_key, "language": self.language}
        async with session.get(f"{TMDB_BASE_URL}/tv/{tv_id}/season/{season_number}", params=params, proxy=self.proxy,
                               ssl=False) as resp:
            season_data = await resp.json()

        root = ET.Element("season")
        ET.SubElement(root, "seasonnumber").text = str(season_number)
        ET.SubElement(root, "title").text = season_data.get("name", f"Season {season_number}")
        ET.SubElement(root, "plot").text = season_data.get("overview", "")

        xml_str = ET.tostring(root, encoding='utf-8')
        pretty_xml = minidom.parseString(xml_str).toprettyxml(indent="  ", encoding='utf-8')
        async with aiofiles.open(nfo_path, "wb") as f:
            await f.write(pretty_xml)
        log_with_tqdm(f"成功生成 SEASON NFO: {nfo_path}", "info")

        # 下载季海报和剧照
        await self.download_season_images(session, tv_id, season_number, folder)

    async def generate_episode_nfo(self, session: aiohttp.ClientSession, tv_id: int, season: int, episode: int, filepath: str):
        params = {"api_key": self.api_key, "language": self.language}
        async with session.get(f"{TMDB_BASE_URL}/tv/{tv_id}/season/{season}/episode/{episode}", params=params, proxy=self.proxy, ssl=False) as resp:
            ep_data = await resp.json()

        root = ET.Element("episodedetails")
        ET.SubElement(root, "title").text = ep_data.get("name", "")
        ET.SubElement(root, "season").text = str(season)
        ET.SubElement(root, "episode").text = str(episode)
        ET.SubElement(root, "plot").text = ep_data.get("overview", "")
        ET.SubElement(root, "aired").text = ep_data.get("air_date", "")

        nfo_path = os.path.splitext(filepath)[0] + ".nfo"
        xml_str = ET.tostring(root, encoding='utf-8')
        pretty_xml = minidom.parseString(xml_str).toprettyxml(indent="  ", encoding='utf-8')
        async with aiofiles.open(nfo_path, "wb") as f:
            await f.write(pretty_xml)
        log_with_tqdm(f"成功生成 EPISODE NFO: {nfo_path}", "info")

        # 下载剧集剧照
        folder = os.path.dirname(filepath)
        await self.download_episode_image(session, tv_id, season, episode, folder, filepath)

    async def process_tv_file(self, session: aiohttp.ClientSession, filepath: str):
        filename = os.path.basename(filepath)
        nfo_path = os.path.splitext(filepath)[0] + '.nfo'
        if os.path.exists(nfo_path):
            log_with_tqdm(f"已存在NFO，跳过: {nfo_path}", "info")
            return

        parsed = self.parse_tv(filename)
        if not parsed:
            return

        show_name, season, episode = parsed

        tv_data = await self.search_tv(session, show_name)
        if not tv_data:
            return

        credits = await self.get_tv_credits(session, tv_data['id'])

        # 生成 tvshow.nfo（只生成一次）
        tvshow_folder = os.path.dirname(os.path.dirname(filepath))
        if tvshow_folder not in self.tvshow_generated_dirs:
            await self.generate_tvshow_nfo(session, tv_data, credits, tvshow_folder)
            self.tvshow_generated_dirs.add(tvshow_folder)

        # 生成 season.nfo（只生成一次）
        season_folder = os.path.dirname(filepath)
        if season_folder not in self.season_generated_dirs:
            await self.generate_season_nfo(session, tv_data['id'], season, season_folder)
            self.season_generated_dirs.add(season_folder)

        # 生成 episode.nfo
        await self.generate_episode_nfo(session, tv_data['id'], season, episode, filepath)

    async def async_run(self):
        tv_files = []
        for media_dir in self.tv_dirs:
            for root, _, files in os.walk(media_dir):
                for name in files:
                    ext = os.path.splitext(name)[1][1:].lower()
                    if ext in self.video_exts_list:
                        tv_files.append(os.path.join(root, name))

        log_with_tqdm(f"共发现 {len(tv_files)} 部剧集文件，开始处理...", "info")
        if not tv_files:
            log_with_tqdm("未发现待处理的剧集文件", "warning")
            return

        rate_limit = 40
        semaphore = asyncio.Semaphore(rate_limit)
        connector = aiohttp.TCPConnector(ssl=False)

        async with aiohttp.ClientSession(connector=connector) as session:
            async def sem_task(f):
                async with semaphore:
                    await asyncio.sleep(1.0 / rate_limit)
                    return await self.process_tv_file(session, f)

            tasks = [sem_task(f) for f in tv_files]
            for f in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc='📺 处理进度', ncols=80, colour='cyan'):
                await f
            print("\n📋 电视剧刮削完成")

        if self.failed_tv:
            log_with_tqdm("以下剧集文件处理失败:", "warning")
            for f in self.failed_tv:
                log_with_tqdm(f" - {f}", "warning")
            with open('failed_tv.log', 'w', encoding='utf-8') as fail_log:
                for f in self.failed_tv:
                    fail_log.write(f + '\n')


class MovieNfoGenerator(BaseNfoGenerator):
    def __init__(self, config_file='config.ini'):
        super().__init__(config_file)
        self.movie_dirs = [d.strip() for d in self.config.get('tmdb', 'movie_dir', fallback='media/电影').split(',') if d.strip()]
        self.failed_movies: List[str] = []

    def parse_movie(self, filename: str) -> Optional[tuple[str, str]]:
        exts = '|'.join([re.escape(e) for e in self.video_exts_list])
        m1 = re.match(rf'(.+)[.](\d{{4}})[.]({exts})$', filename, re.IGNORECASE)
        m2 = re.match(rf'(.+?) \((\d{{4}})\)[.]({exts})$', filename, re.IGNORECASE)
        if m1:
            return m1.groups()[:2]
        if m2:
            return m2.groups()[:2]
        log_with_tqdm(f"无法解析文件名格式: {filename}", "warning")
        self.failed_movies.append(filename)
        return None

    async def search_movie(self, session: aiohttp.ClientSession, title: str, year: str) -> Optional[dict]:
        params = {
            "api_key": self.api_key,
            "query": title,
            "year": year,
            "language": self.language
        }
        async with session.get(f"{TMDB_BASE_URL}/search/movie", params=params, proxy=self.proxy, ssl=False) as resp:
            data = await resp.json()
            for m in data.get("results", []):
                if m["title"] == title and m.get("release_date", "").startswith(str(year)):
                    return m
        log_with_tqdm(f"未找到 TMDb 匹配: {title} ({year})", "warning")
        self.failed_movies.append(f"{title} ({year})")
        return None

    async def get_movie_credits(self, session: aiohttp.ClientSession, movie_id: int) -> dict:
        params = {"api_key": self.api_key, "language": self.language}
        async with session.get(f"{TMDB_BASE_URL}/movie/{movie_id}/credits", params=params, proxy=self.proxy, ssl=False) as resp:
            return await resp.json()

    async def download_image(self, session: aiohttp.ClientSession, url: str, save_path: str):
        if not url:
            log_with_tqdm(f"无效的图片URL，跳过下载: {save_path}", "warning")
            return
        try:
            async with session.get(url, proxy=self.proxy, ssl=False, timeout=15) as resp:
                if resp.status == 200:
                    f = await aiofiles.open(save_path, mode='wb')
                    await f.write(await resp.read())
                    await f.close()
                    log_with_tqdm(f"✅ 成功下载图片: {save_path}", "info")
                else:
                    log_with_tqdm(f"❌ 下载图片失败，状态码: {resp.status}，路径: {save_path}", "warning")
        except Exception as e:
            log_with_tqdm(f"❌ 下载图片异常: {e}，路径: {save_path}", "error")

    async def download_posters(self, session: aiohttp.ClientSession, movie_data: dict, save_dir: str):
        base_url = TMDB_IMAGE_BASE_URL
        poster_path = movie_data.get("poster_path")
        backdrop_path = movie_data.get("backdrop_path")

        if poster_path:
            poster_url = base_url + poster_path
            poster_save_path = os.path.join(save_dir, "poster.jpg")
            await self.download_image(session, poster_url, poster_save_path)
        else:
            log_with_tqdm("没有找到 poster_path，跳过 poster.jpg 下载", "warning")

        if backdrop_path:
            fanart_url = base_url + backdrop_path
            fanart_save_path = os.path.join(save_dir, "fanart.jpg")
            await self.download_image(session, fanart_url, fanart_save_path)
        else:
            log_with_tqdm("没有找到 backdrop_path，跳过 fanart.jpg 下载", "warning")

    async def generate_movie_nfo(self, movie_data: dict, credits: dict, original_file_path: str, session: aiohttp.ClientSession = None):
        root = ET.Element("movie")
        ET.SubElement(root, "title").text = movie_data["title"]
        ET.SubElement(root, "originaltitle").text = movie_data.get("original_title", movie_data["title"])
        ET.SubElement(root, "sorttitle").text = movie_data["title"]
        ET.SubElement(root, "year").text = movie_data.get("release_date", '')[:4]
        ET.SubElement(root, "releasedate").text = movie_data.get("release_date", '')
        ET.SubElement(root, "plot").text = movie_data.get("overview", '')
        ET.SubElement(root, "runtime").text = str(movie_data.get("runtime", ''))
        ET.SubElement(root, "rating").text = str(movie_data.get("vote_average", ''))
        ET.SubElement(root, "id").text = str(movie_data["id"])

        for director in [c['name'] for c in credits.get('crew', []) if c.get('job') == 'Director']:
            ET.SubElement(root, "director").text = director

        for actor in credits.get('cast', []):
            actor_el = ET.SubElement(root, "actor")
            ET.SubElement(actor_el, "name").text = actor.get('name', '')
            ET.SubElement(actor_el, "role").text = actor.get('character', '')
            ET.SubElement(actor_el, "order").text = str(actor.get('order', ''))

        nfo_path = os.path.splitext(original_file_path)[0] + '.nfo'
        xml_str = ET.tostring(root, encoding='utf-8')
        pretty_xml = minidom.parseString(xml_str).toprettyxml(indent="  ", encoding='utf-8')
        with open(nfo_path, "wb") as f:
            f.write(pretty_xml)
        log_with_tqdm(f"成功生成: {nfo_path}", "info")

        # 下载 poster 和 fanart
        if session:
            save_dir = os.path.dirname(original_file_path)
            await self.download_posters(session, movie_data, save_dir)

    async def process_movie_file(self, session: aiohttp.ClientSession, filepath: str):
        filename = os.path.basename(filepath)
        nfo_path = os.path.splitext(filepath)[0] + '.nfo'
        if os.path.exists(nfo_path):
            log_with_tqdm(f"已存在NFO，跳过: {nfo_path}", "info")
            return
        try:
            result = self.parse_movie(filename)
            if not result:
                return
            movie_name, year = result
            movie = await self.search_movie(session, movie_name, year)
            if not movie:
                return
            credits = await self.get_movie_credits(session, movie['id'])
            log_with_tqdm(f"匹配成功: {movie['title']} ({movie.get('release_date', '')})", "info")
            await self.generate_movie_nfo(movie, credits, filepath, session)
        except Exception as e:
            self.failed_movies.append(filename)
            log_with_tqdm(f"处理出错: {filename} | {e}", "error")

    async def async_run(self):
        movie_files = []
        for media_dir in self.movie_dirs:
            for root, _, files in os.walk(media_dir):
                for name in files:
                    ext = os.path.splitext(name)[1][1:].lower()
                    if ext in self.video_exts_list:
                        movie_files.append(os.path.join(root, name))

        log_with_tqdm(f"共发现 {len(movie_files)} 部电影，开始处理...", "info")
        if not movie_files:
            log_with_tqdm("未发现待处理的电影", "warning")
            return

        rate_limit = 40
        semaphore = asyncio.Semaphore(rate_limit)
        connector = aiohttp.TCPConnector(ssl=False)

        async with aiohttp.ClientSession(connector=connector) as session:
            async def sem_task(f):
                async with semaphore:
                    await asyncio.sleep(1.0 / rate_limit)
                    return await self.process_movie_file(session, f)

            tasks = [sem_task(f) for f in movie_files]
            for f in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc='📦 处理进度', ncols=80, colour='green'):
                await f
            print("\n📋 处理完成")

        if self.failed_movies:
            log_with_tqdm("以下文件处理失败:", "warning")
            for f in self.failed_movies:
                log_with_tqdm(f" - {f}", "warning")

            with open('failed_movie.log', 'w', encoding='utf-8') as fail_log:
                for f in self.failed_movies:
                    fail_log.write(f + '\n')



async def check_tmdb_connectivity(api_key: str, proxy: Optional[str] = None) -> bool:
    test_endpoints = [
        ("TMDb API", f"{TMDB_BASE_URL}/configuration?api_key={api_key}"),
        ("TMDb 图片服务", f"{TMDB_IMAGE_BASE_URL}/nonexist.jpg"),  # 不存在也能返回404
    ]

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        for name, url in test_endpoints:
            try:
                async with session.get(url, proxy=proxy, timeout=10) as resp:
                    if resp.status in [200, 401, 404]:
                        log_with_tqdm(f"{name} ✅ 连通正常（状态码 {resp.status}）", "info")
                    else:
                        log_with_tqdm(f"{name} ❌ 状态异常（状态码 {resp.status}）", "error")
                        return False
            except Exception as e:
                log_with_tqdm(f"{name} ❌ 连接失败: {e}", "error")
                return False

        # 额外检查 API Key
        try:
            async with session.get(f"{TMDB_BASE_URL}/movie/550", params={"api_key": api_key}, proxy=proxy, timeout=10) as resp:
                if resp.status == 401:
                    log_with_tqdm("❌ API Key 无效，请检查 config.ini", "error")
                    return False
                elif resp.status == 200:
                    log_with_tqdm("✅ API Key 有效", "info")
                else:
                    log_with_tqdm(f"API Key 检查状态异常: {resp.status}", "error")
                    return False
        except Exception as e:
            log_with_tqdm(f"检查 API Key 失败: {e}", "error")
            return False

    return True


if __name__ == '__main__':
    import sys
    async def main():
        # 提前读取配置文件
        config = configparser.ConfigParser()
        config.read("config.ini", encoding="utf-8")
        api_key = config['tmdb']['api_key']
        proxy = config['proxy'].get('https', None) if 'proxy' in config else None

        # 先进行连接与API Key检查
        ok = await check_tmdb_connectivity(api_key, proxy)
        if not ok:
            log_with_tqdm("❌ API Key 校验或网络连接失败，程序终止", "error")
            return

        # 初始化并运行
        movie_generator = MovieNfoGenerator()
        tv_generator = TvNfoGenerator()

        await asyncio.gather(
            movie_generator.async_run(),
            tv_generator.async_run(),
        )

    if sys.version_info >= (3, 7):
        asyncio.run(main())
