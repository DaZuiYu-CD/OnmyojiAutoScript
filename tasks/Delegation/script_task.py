# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from time import sleep
from datetime import time, datetime, timedelta

from module.logger import logger
from module.exception import (
    TaskEnd,
    GamePageUnknownError,
    GameTooManyClickError,
    GameStuckError,
)
from module.base.timer import Timer

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_delegation
from tasks.Delegation.config import DelegationConfig
from tasks.Delegation.assets import DelegationAssets


class ScriptTask(GameUi, DelegationAssets):

    def run(self):
        # 委派页导航失败计数: 连续失败 NAV_FAIL_LIMIT 次放弃本次任务, 不再触发 Restart 无限循环
        # 根因(8-12/8-13 老区号): 委派页锚点 I_CHECK_DELEGATION 曾零容错失配, goto_page 反复
        # Transition cannot reach(每轮 8s) -> 往返 6 轮触发 GameTooManyClickError -> script.py
        # 捕获后 task_call('Restart') 只重启游戏 -> Delegation next_run 仍过期(2023-01-01) ->
        # 调度器立即再跑 -> 死循环 1 小时(penalty 88)。失败路径从不经过 set_next_run, 故无排期兜底。
        # 这里在任务层兜底: 导航异常捕获计数, 达阈值 set_next_run(success=False) 走 failure_interval
        # (1 天) 顺延到明天, 然后 TaskEnd 正常结束, 不抛回调度器, 从源头断掉重启循环。
        nav_fail_limit = 3
        for attempt in range(1, nav_fail_limit + 1):
            try:
                self.goto_page(page_delegation)
                break
            except (GamePageUnknownError, GameTooManyClickError, GameStuckError) as e:
                logger.warning(f'Delegation: goto page_delegation 第 {attempt}/{nav_fail_limit} 次失败: {e}')
                if attempt >= nav_fail_limit:
                    logger.warning(
                        f'Delegation: 连续 {nav_fail_limit} 次导航失败, 放弃本次任务, 顺延到下次'
                    )
                    self.set_next_run(task='Delegation', finish=True, success=False)
                    raise TaskEnd
                self.device.sleep(5)
        self.check_reward()
        con: DelegationConfig = self.config.delegation.delegation_config
        if con.miyoshino_painting:
            self.delegate_one('画')
        if con.bird_feather:
            self.delegate_one('鸟羽')
        if con.find_earring:
            self.delegate_one('寻找耳环')
        if con.cat_boss:
            self.delegate_one('猫老大')
        if con.miyoshino:
            self.delegate_one('接送')
        if con.strange_trace:
            self.delegate_one('痕迹')


        self.set_next_run(task='Delegation', success=True, finish=True)
        raise TaskEnd

    def delegate_one(self, name: str) -> bool:
        """
        委派一个任务
        :param name:
        :return:
        """
        def ui_click(click, stop):
            while 1:
                self.screenshot()
                if self.appear(stop):
                    break
                if self.click(click, interval=1.5):
                    continue
        logger.hr('Delegation one', 2)
        self.O_D_NAME.keyword = name
        self.screenshot()
        if not self.ocr_appear(self.O_D_NAME):
            logger.warning(f'Delegation: {name} not found')
            return False
        while 1:
            self.screenshot()
            if self.appear(self.I_D_START):
                break
            # 如果出现’召回‘ ’返回‘ 说明这个是现在委派中
            # 需要退出
            if self.appear(self.I_D_BACK):
                logger.warning(f'Delegation: {name} is in delegation')
                self.ui_click_until_disappear(self.I_D_BACK)
                self.wait_until_appear(self.I_REWARDS_MIN)
                return False
            if self.appear_then_click(self.I_D_SKIP, interval=0.8):
                continue
            if self.appear_then_click(self.I_D_CONFIRM, interval=0.8):
                continue
            if self.ocr_appear_click(self.O_D_NAME, interval=1):
                continue
        # 进入委派  fefe e  fe
        logger.info(f'Enter Delegation: {name}')
        ui_click(self.C_D_1, self.I_D_SELECT_1)
        ui_click(self.C_D_2, self.I_D_SELECT_2)
        ui_click(self.C_D_3, self.I_D_SELECT_3)
        ui_click(self.C_D_4, self.I_D_SELECT_4)
        # 委派开始
        logger.info(f'Delegation: {name} start')
        while 1:
            self.screenshot()
            if not self.appear(self.I_D_START):
                break
            if self.click(self.C_D_5, interval=0.8):
                continue
            if self.appear_then_click(self.I_D_START, interval=1.8):
                continue
        # ui_click(self.C_D_5, self.I_D_SELECT_5)
        # self.ui_click_until_disappear(self.I_D_START)

    def check_reward(self):
        check_timer = Timer(3)
        check_timer.start()
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_REWARDS_GET, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_REWARDS_CHAT, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_CHAT_1, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_CHAT_2, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_REWARDS_DONE, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_REWARDS_FALSE, interval=1):
                check_timer.reset()
                continue


            if not self.appear(self.I_REWARDS_MIN):
                continue
            if check_timer.reached():
                break
            if self.ocr_appear_click(self.O_D_DONE, interval=1):
                check_timer.reset()
                continue


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    from memory_profiler import profile
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)

    # t.delegate_one('弥助的画')
    t.run()




