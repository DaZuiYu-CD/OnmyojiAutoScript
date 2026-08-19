# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import numpy as np

from module.base.decorator import cached_property
from module.logger import logger


class RuleClick:

    def __init__(self, roi_front: tuple, roi_back: tuple, name: str = None) -> None:
        """
        初始化
        :param roi_front:
        :param roi_back:
        """
        self.roi_front = roi_front
        self.roi_back = roi_back
        if name:
            self.name = name
        else:
            self.name = 'click'

    def coord(self) -> tuple:
        """
        获取坐标, 从roi_front随机获取坐标
        :return:
        """
        x, y, w, h = self.roi_front
        x = np.random.randint(x, x + w)
        y = np.random.randint(y, y + h)
        return x, y

    def coord_more(self) -> tuple:
        """
        从roi_back随机获取坐标
        :return:
        """
        x, y, w, h = self.roi_back
        x = np.random.randint(x, x + w)
        y = np.random.randint(y, y + h)
        return x, y

    @property
    def center(self) -> tuple:
        """
        返回roi_front的中心坐标
        :return:
        """
        x, y, w, h = self.roi_front
        return x + w // 2, y + h // 2

    def center_offset(self, offset: int = 20) -> tuple:
        """
        返回roi_front中心坐标并附加随机偏移, 模拟人手点击的微小抖动。

        与 coord()(整个 roi_front 内随机) 不同: 以中心为锚仅在 ±offset 像素
        小范围内随机, 兼顾"精确命中按钮"与"位置不固定防检测"。适用于
        roi_front 远大于按钮热区的资产(点击随机到按钮外会落空)。

        Args:
            offset: 中心向四周的最大偏移像素数, 默认 20。

        Returns:
            tuple[int, int]: 点击坐标 (x, y), 已 clamp 到图像边界。
        """
        cx, cy = self.center
        ox = np.random.randint(-offset, offset + 1)
        oy = np.random.randint(-offset, offset + 1)
        return max(0, cx + ox), max(0, cy + oy)

    def move(self, x: int, y: int) -> None:
        """
        移动roi_front, 需要限幅x是0-1280, y是0-720
        :param x:
        :param y:
        :return:
        """
        x, y, w, h = self.roi_front
        x += x
        y += y
        if x <= 0:
            x = 0
        elif x >= 1280:
            x = 1280

        if y <= 0:
            y = 0
        elif y >= 720:
            y = 720

        self.roi_front = x, y, w, h

    def __repr__(self):
        return self.name
