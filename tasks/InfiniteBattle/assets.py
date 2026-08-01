# This Python file uses the following encoding: utf-8
# 无限战斗模式资产: 只新增自定义挑战按钮的动态加载, 内置按钮直接复用各任务包模板
from pathlib import Path

from module.atom.image import RuleImage
from module.logger import logger

RES_DIR = Path('./tasks/InfiniteBattle/res')

# 自定义挑战按钮的搜索区域: 右下角(与活动挑战按钮同款区域)
CUSTOM_FIRE_ROI_BACK = (1080, 530, 192, 190)
CUSTOM_FIRE_THRESHOLD = 0.8


def load_custom_fire(file_name: str) -> RuleImage | None:
    """
    从 tasks/InfiniteBattle/res/ 目录加载用户自定义的挑战按钮图片
    :param file_name: 文件名(不含目录), 例如 my_fire.png
    :return: RuleImage 或 None(文件不存在/未配置)
    """
    if not file_name:
        return None
    path = RES_DIR / file_name
    if not path.exists():
        logger.warning(f'Custom challenge button image not found: {path}')
        return None
    # name 由 file 名自动派生(cached_property), roi_front 初始给一个右下角默认位置, 加载模板后会自动校正尺寸
    return RuleImage(roi_front=(1132, 602, 84, 45), roi_back=CUSTOM_FIRE_ROI_BACK,
                     threshold=CUSTOM_FIRE_THRESHOLD, method='Template matching',
                     file=str(path))
