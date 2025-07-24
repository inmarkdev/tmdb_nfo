import os
import sys
import subprocess
import json
import requests
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from guessit import guessit  # 用于智能解析视频文件名获取标题、年份、季集信息等
from lxml import etree       # 用于生成 .nfo 的 XML 文件
import tmdbsimple as tmdb    # TMDb API 客户端

# === 配置项 ===
MEDIA_PATH = os.getenv('MEDIA_PATH', r'').strip()  # 媒体文件目录，从环境变量读取
tmdb.API_KEY = os.getenv('TMDB_API_KEY', '')       # TMDb API Key
DOWNLOAD_IMAGES = os.getenv('DOWNLOAD_IMAGES', 'true').lower() == 'true'  # 是否下载海报和背景图
DOWNLOAD_ACTOR_IMAGES = os.getenv('DOWNLOAD_ACTOR_IMAGES', 'true').lower() == 'true'  # 是否下载演员头像
LANGUAGE = 'zh-CN'           # TMDb API语言
VIDEO_EXT = ['.mp4', '.mkv', '.avi', '.mov', '.strm']  # 支持的视频格式
MAX_WORKERS = 4              # 并发线程数
TMDB_IMAGE_BASE = 'https://image.tmdb.org/t/p/original'  # TMDb 图片基础路径

# === 日志配置 ===
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# 下载图片（海报、背景、剧照）
def download_image(tmdb_path, save_path):
    if not DOWNLOAD_IMAGES or not tmdb_path:
        return
    if os.path.exists(save_path):
        logging.info(f"已存在: {save_path}")
        return
    try:
        url = TMDB_IMAGE_BASE + tmdb_path
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            with open(save_path, 'wb') as f:
                f.write(r.content)
            logging.info(f"下载图片: {save_path}")
        else:
            logging.warning(f"图片下载失败 ({r.status_code}): {url}")
    except Exception as e:
        logging.warning(f"请求异常: {e}")

# 批量下载演员头像
def download_actor_images(actor_list, target_dir):
    if not DOWNLOAD_ACTOR_IMAGES:
        return
    os.makedirs(target_dir, exist_ok=True)
    for actor in actor_list:
        img_path = actor.get('profile_path')
        name = actor.get('name')
        if not img_path or not name:
            continue
        clean_name = name.replace('/', '_')  # 处理非法路径字符
        save_path = os.path.join(target_dir, f"{clean_name}.jpg")
        if os.path.exists(save_path):
            logging.info(f"已存在演员头像: {save_path}")
            continue
        download_image(img_path, save_path)

# 使用 ffprobe 提取媒体信息（视频/音频编解码信息、时长等）
def ffprobe_get_media_info(filepath):
    try:
        command = [
            'ffprobe', '-v', 'error',
            '-print_format', 'json',
            '-show_format', '-show_streams',
            filepath
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        if result.returncode != 0:
            logging.warning(f"ffprobe 错误: {result.stderr}")
            return None

        info = json.loads(result.stdout)

        video_streams = [s for s in info.get('streams', []) if s.get('codec_type') == 'video']
        audio_streams = [s for s in info.get('streams', []) if s.get('codec_type') == 'audio']
        duration = float(info.get('format', {}).get('duration', 0))

        return {
            'duration': duration,
            'video': video_streams,
            'audio': audio_streams
        }

    except Exception as e:
        logging.error(f"ffprobe 解析失败: {e}")
        return None

# 在 TMDb 搜索结果中挑选最佳匹配项
def best_match(results, title, year=None, is_tv=False):
    title = title.lower()
    for r in results:
        r_title = r.get('name') if is_tv else r.get('title')
        r_orig = r.get('original_name') if is_tv else r.get('original_title')
        r_date = r.get('first_air_date' if is_tv else 'release_date', '')
        if (r_title and r_title.lower() == title) or (r_orig and r_orig.lower() == title):
            if not year or (r_date and r_date.startswith(str(year))):
                return r
    return results[0] if results else None

# 调用 TMDb API 搜索电影或剧集信息
def search_tmdb(title, year=None, is_tv=False):
    search = tmdb.Search()
    response = search.tv(query=title, year=year, language=LANGUAGE) if is_tv else search.movie(query=title, year=year, language=LANGUAGE)
    return best_match(response['results'], title, year, is_tv)

# 处理单个媒体文件：生成对应 .nfo 文件和下载图片
def process_file(filepath):
    info = guessit(os.path.basename(filepath))  # 解析文件名
    title = info.get('title')
    year = info.get('year')
    is_tv = 'season' in info and 'episode' in info

    if not title:
        logging.error(f"无法解析标题：{filepath}")
        return

    logging.info(f"查找: {title} {'(剧集)' if is_tv else '(电影)'}")

    result = search_tmdb(title, year, is_tv)
    if not result:
        logging.error(f"TMDb 未找到：{title}")
        return

    nfo_path = filepath.rsplit('.', 1)[0] + '.nfo'
    nfo_exists = os.path.exists(nfo_path)

    try:
        if is_tv:
            # 处理剧集
            tv = tmdb.TV(result['id'])
            tv_info = tv.info(language=LANGUAGE, append_to_response='external_ids')
            credits = tv.credits(language=LANGUAGE)
            actors = credits.get('cast', [])

            season_num = info['season']
            episode_num = info['episode']
            season = tmdb.TV_Seasons(result['id'], season_num)
            season_info = season.info(language=LANGUAGE)
            episode = next((e for e in season_info['episodes'] if e['episode_number'] == episode_num), None)

            show_dir = os.path.dirname(os.path.dirname(nfo_path))
            episode_dir = os.path.dirname(filepath)

            # 生成 tvshow.nfo（整部剧的信息）
            tvshow_nfo_path = os.path.join(show_dir, 'tvshow.nfo')
            if not os.path.exists(tvshow_nfo_path):
                tv_root = etree.Element('tvshow')
                etree.SubElement(tv_root, 'title').text = tv_info.get('name', '')
                etree.SubElement(tv_root, 'sorttitle').text = tv_info.get('name', '')
                etree.SubElement(tv_root, 'showtitle').text = tv_info.get('name', '')
                etree.SubElement(tv_root, 'year').text = tv_info.get('first_air_date', '')[:4]
                etree.SubElement(tv_root, 'plot').text = etree.CDATA(tv_info.get('overview', ''))
                etree.SubElement(tv_root, 'tmdbid').text = str(tv_info.get('id'))
                etree.SubElement(tv_root, 'imdbid').text = tv_info.get('external_ids', {}).get('imdb_id', '')
                etree.SubElement(tv_root, 'premiered').text = tv_info.get('first_air_date', '')
                etree.SubElement(tv_root, 'dateadded').text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                etree.ElementTree(tv_root).write(tvshow_nfo_path, encoding='utf-8', pretty_print=True, xml_declaration=True)
                logging.info(f"TVShow NFO: {tvshow_nfo_path}")
            else:
                logging.info(f"已存在 TVShow NFO: {tvshow_nfo_path}")

            # 下载剧集图片
            if DOWNLOAD_IMAGES:
                download_image(tv_info.get('poster_path'), os.path.join(show_dir, 'poster.jpg'))
                download_image(tv_info.get('backdrop_path'), os.path.join(show_dir, 'fanart.jpg'))
                season_poster = next((s for s in tv_info.get('seasons', []) if s.get('season_number') == season_num), None)
                if season_poster:
                    download_image(season_poster.get('poster_path'), os.path.join(show_dir, f'season{season_num:02d}-poster.jpg'))
                if episode and episode.get('still_path'):
                    episode_filename = os.path.splitext(os.path.basename(filepath))[0]
                    episode_poster_path = os.path.join(episode_dir, f"{episode_filename}-poster.jpg")
                    download_image(episode.get('still_path'), episode_poster_path)

            if DOWNLOAD_ACTOR_IMAGES:
                download_actor_images(actors, os.path.join(show_dir, '.actors'))

            # 生成 episode.nfo（单集信息）
            if not nfo_exists and episode:
                media_info = ffprobe_get_media_info(filepath)
                root = etree.Element('episodedetails')
                etree.SubElement(root, 'title').text = episode.get('name', '')
                etree.SubElement(root, 'season').text = str(season_num)
                etree.SubElement(root, 'episode').text = str(episode_num)
                etree.SubElement(root, 'aired').text = episode.get('air_date', '')
                etree.SubElement(root, 'plot').text = etree.CDATA(episode.get('overview', ''))
                etree.SubElement(root, 'rating').text = str(episode.get('vote_average', ''))
                etree.SubElement(root, 'showtitle').text = tv_info.get('name', '')
                etree.SubElement(root, 'year').text = tv_info.get('first_air_date', '')[:4]
                etree.SubElement(root, 'premiered').text = episode.get('air_date', '')
                etree.SubElement(root, 'dateadded').text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                etree.SubElement(root, 'runtime').text = str(int(media_info['duration'] // 60)) if media_info else ''
                etree.SubElement(root, 'video_codec').text = media_info['video'][0]['codec_name'] if media_info and media_info['video'] else ''
                etree.SubElement(root, 'audio_codec').text = media_info['audio'][0]['codec_name'] if media_info and media_info['audio'] else ''
                etree.ElementTree(root).write(nfo_path, encoding='utf-8', pretty_print=True, xml_declaration=True)
                logging.info(f"Episode NFO: {nfo_path}")
            elif nfo_exists:
                logging.info(f"已存在 NFO: {nfo_path}")

        else:
            # 处理电影
            movie = tmdb.Movies(result['id'])
            movie_info = movie.info(language=LANGUAGE, append_to_response='videos,external_ids')
            credits = movie.credits(language=LANGUAGE)
            actors = credits.get('cast', [])
            movie_dir = os.path.dirname(nfo_path)

            if DOWNLOAD_IMAGES:
                download_image(movie_info.get('poster_path'), os.path.join(movie_dir, 'poster.jpg'))
                download_image(movie_info.get('backdrop_path'), os.path.join(movie_dir, 'fanart.jpg'))
            if DOWNLOAD_ACTOR_IMAGES:
                download_actor_images(actors, os.path.join(movie_dir, '.actors'))

            if not nfo_exists:
                media_info = ffprobe_get_media_info(filepath)
                root = etree.Element('movie')
                etree.SubElement(root, 'title').text = movie_info.get('title', '')
                etree.SubElement(root, 'year').text = movie_info.get('release_date', '')[:4]
                etree.SubElement(root, 'plot').text = etree.CDATA(movie_info.get('overview', ''))
                etree.SubElement(root, 'tmdbid').text = str(movie_info.get('id'))
                etree.SubElement(root, 'imdbid').text = movie_info.get('imdb_id', '')
                etree.SubElement(root, 'dateadded').text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                etree.SubElement(root, 'runtime').text = str(int(media_info['duration'] // 60)) if media_info else ''
                etree.SubElement(root, 'video_codec').text = media_info['video'][0]['codec_name'] if media_info and media_info['video'] else ''
                etree.SubElement(root, 'audio_codec').text = media_info['audio'][0]['codec_name'] if media_info and media_info['audio'] else ''
                etree.ElementTree(root).write(nfo_path, encoding='utf-8', pretty_print=True, xml_declaration=True)
                logging.info(f"Movie NFO: {nfo_path}")
            else:
                logging.info(f"已存在 NFO: {nfo_path}")

    except Exception as e:
        logging.error(f"处理失败: {filepath} | 错误: {e}")

# 并发扫描媒体目录并处理所有文件
def scan_directory_concurrent(folder):
    files = []
    for root_dir, _, filenames in os.walk(folder):
        for f in filenames:
            if os.path.splitext(f)[1].lower() in VIDEO_EXT:
                files.append(os.path.join(root_dir, f))

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(process_file, file) for file in files]
        for _ in as_completed(futures):
            pass

# 检查 TMDb API Key 是否可用
def validate_tmdb_key():
    try:
        search = tmdb.Search()
        result = search.movie(query="test")
        if 'results' not in result:
            logging.error("TMDB API KEY 无效或请求失败")
            sys.exit(1)
    except Exception as e:
        logging.error(f"TMDB API KEY 验证失败: {e}")
        sys.exit(1)

# 程序入口
if __name__ == '__main__':
    validate_tmdb_key()

    if not MEDIA_PATH:
        MEDIA_PATH = input("请输入视频目录路径：").strip()

    if os.path.isdir(MEDIA_PATH):
        scan_directory_concurrent(MEDIA_PATH)
    else:
        logging.error("路径无效")
