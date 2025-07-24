from tmdb_tv import TVShowNfoGenerator
from tmdb_movie import MovieNfoGenerator
import re

def is_tv_show(input_str):
    """
    判断是否是电视剧输入格式：剧名 (年份) SxxExx
    """
    pattern = r"^(.*?)\s*\((\d{4})\)\s*[sS](\d{2})[eE](\d{2})$"
    return re.match(pattern, input_str.strip()) is not None

def is_movie(input_str):
    """
    判断是否是电影输入格式：电影名 (年份)
    """
    pattern = r"^(.*?)\s*\((\d{4})\)$"
    if is_tv_show(input_str):
        return False
    return re.match(pattern, input_str.strip()) is not None

def main():
    print("影视 NFO 生成器，输入 exit 或 quit 退出程序。")
    tv_generator = TVShowNfoGenerator()
    movie_generator = MovieNfoGenerator()

    while True:
        input_str = input("\n请输入影视名称（电影格式：电影名 (年份)，电视剧格式：剧名 (年份) SxxExx）：").strip()
        if input_str.lower() in ('exit', 'quit'):
            print("程序退出，感谢使用！")
            break

        if is_tv_show(input_str):
            print("检测到输入为电视剧，开始生成电视剧 NFO 文件...")
            tv_generator.run(input_str)
        elif is_movie(input_str):
            print("检测到输入为电影，开始生成电影 NFO 文件...")
            movie_generator.run(input_str)
        else:
            print("输入格式无法识别，请输入电影名 (年份) 或 剧名 (年份) SxxExx 格式。")

if __name__ == "__main__":
    main()
