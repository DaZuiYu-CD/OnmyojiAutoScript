# This Python file uses the following encoding: utf-8
# 无限战斗模式: 用户已就位(选好副本/组好队), 脚本只循环 挑战->战斗->结算
# 设计要点:
#   1. 战斗四页(准备/战斗/结算/奖励)全部交给 run_general_battle 现有状态机
#   2. 入口只认"挑战按钮"(内置御魂/爬塔模板 + 用户自定义图片 + OCR"挑战"兜底)
#   3. 无进展看门狗: 超时不重启游戏, 温和退出任务, 按 failure_interval 后再试
import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2

from module.atom.image import RuleImage
from module.exception import TaskEnd
from module.logger import logger
from tasks.ActivityShikigami.assets import ActivityShikigamiAssets
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralBattle.general_battle import BattleAction, BattleContext, GeneralBattle
from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import any_of, page_reward
from tasks.InfiniteBattle.assets import load_custom_fire
from tasks.InfiniteBattle.config import ChallengeButton, UserStatus
from tasks.Orochi.assets import OrochiAssets


class ScriptTask(GeneralBattle, GeneralInvite, GameUi):
    """无限战斗模式: 循环挑战直到次数/时间到限, 或看门狗判定异常"""

    def _register_custom_pages(self) -> None:
        # 战后"是否继续邀请队友"弹窗和猫咪奖励也算结算页的一种, 防止状态机不认识
        reward_page = self.navigator.resolve_page(page_reward)
        if reward_page is None:
            return
        reward_page.recognizer = any_of(self.I_GI_SURE, OrochiAssets.I_PET_PRESENT,
                                        reward_page.recognizer)

    def _handle_reward(self, context: BattleContext, config: GeneralBattleConfig) -> BattleAction:
        # 队长战后会出现"是否邀请一次队友", 勾选默认邀请并确认, 回到房间继续循环
        if self.config.infinite_battle.infinite_battle_config.user_status == UserStatus.LEADER and \
                self.check_and_invite(self.config.infinite_battle.infinite_battle_config.default_invite):
            return BattleAction.CONTINUE
        return super()._handle_reward(context, config)

    def run(self) -> None:
        ib_config = self.config.infinite_battle.infinite_battle_config
        gb_config = self.config.infinite_battle.general_battle_config

        self.current_count = 0
        self.limit_count: int = ib_config.limit_count
        self.limit_time: timedelta = timedelta(hours=ib_config.limit_time.hour,
                                               minutes=ib_config.limit_time.minute,
                                               seconds=ib_config.limit_time.second)
        self._last_progress: float = time.time()
        self._custom_fire: RuleImage | None = None
        if ib_config.challenge_button == ChallengeButton.CUSTOM:
            self._custom_fire = load_custom_fire(ib_config.custom_challenge_path)
            if self._custom_fire is None:
                logger.warning('Custom challenge image invalid, fallback to OCR only')

        # 挂上长战斗卡死保护(60s->300s), 让我们的看门狗(默认180s)先于底层强杀触发
        self.device.stuck_record_add('BATTLE_STATUS_S')
        logger.hr('Infinite battle start', 1)
        logger.info(f'Role: {ib_config.user_status}, limit: {self.limit_count} rounds / {self.limit_time}')

        success = True
        try:
            while 1:
                self.screenshot()

                # ---------- 退出条件 ----------
                if self.current_count >= self.limit_count:
                    logger.info('Infinite battle count limit out')
                    break
                if datetime.now() - self.start_time >= self.limit_time:
                    logger.info('Infinite battle time limit out')
                    break

                # ---------- 弹窗/邀请 ----------
                if ib_config.accept_invite_enable and self.check_then_accept():
                    logger.info('Accepted coop/seal invite')
                    self._mark_progress()
                    continue
                if self.appear_then_click(OrochiAssets.I_PET_PRESENT,
                                          action=self.C_RANDOM_RIGHT, interval=1):
                    continue

                # ---------- 战斗四页: 交给通用战斗状态机 ----------
                if self.is_in_battle(is_screenshot=False):
                    self._mark_progress()
                    self.run_general_battle(config=gb_config, battle_key='infinite_battle')
                    logger.hr(f'Round done, total: {self.current_count}', 2)
                    continue

                # ---------- 房间状态(等待是正常状态, 刷新看门狗) ----------
                if self.is_in_room(is_screenshot=False):
                    self._mark_progress()
                    if ib_config.user_status == UserStatus.MEMBER:
                        # 队员不点挑战, 等队长开
                        continue

                # ---------- 入口: 点挑战 ----------
                if ib_config.user_status != UserStatus.MEMBER and self._click_challenge(ib_config):
                    self._mark_progress()
                    continue

                # ---------- 看门狗 ----------
                idle = time.time() - self._last_progress
                if idle > ib_config.no_progress_timeout:
                    logger.warning(f'No progress for {int(idle)}s, treat as abnormal and end task gently')
                    self._save_watchdog_screenshot()
                    success = False
                    break
        finally:
            self.device.stuck_record_clear()

        # ---------- 收尾 ----------
        spent = datetime.now() - self.start_time
        logger.hr('Infinite battle end', 1)
        logger.info(f'Total rounds: {self.current_count}, spent: {spent}, success: {success}')
        if success:
            self.set_next_run('InfiniteBattle', finish=True, success=True)
        else:
            # 温和退出: 不重启游戏, 按失败间隔后再试(不反顶号)
            self.set_next_run('InfiniteBattle', finish=False, success=False)
        raise TaskEnd

    def _mark_progress(self) -> None:
        self._last_progress = time.time()

    def _click_challenge(self, ib_config) -> bool:
        """
        尝试点击挑战按钮: 内置模板 -> 自定义模板 -> OCR"挑战"文字兜底(右下角)
        :return: 是否发生了点击
        """
        match ib_config.challenge_button:
            case ChallengeButton.OROCHI:
                # 房间内挑战按钮(队长) + 御魂副本详情页挑战按钮
                if self.appear_then_click(self.I_FIRE, interval=1):
                    return True
                if self.appear_then_click(OrochiAssets.I_OROCHI_FIRE, interval=1):
                    return True
            case ChallengeButton.ACTIVITY:
                if self.appear_then_click(ActivityShikigamiAssets.I_ACT_FIRE, interval=1):
                    return True
            case ChallengeButton.CUSTOM:
                if self._custom_fire is not None and self.appear_then_click(self._custom_fire, interval=1):
                    return True
        # OCR 兜底: 不认图只认字, 扫右下角区域
        if self.ocr_appear_click(ActivityShikigamiAssets.O_FIRE, interval=1.5):
            return True
        return False

    def _save_watchdog_screenshot(self) -> None:
        try:
            save_dir = Path('./log/infinite_battle')
            save_dir.mkdir(parents=True, exist_ok=True)
            file = save_dir / f'{datetime.now().strftime("%Y-%m-%d_%H-%M-%S")}_watchdog.png'
            cv2.imwrite(str(file), self.device.image)
            logger.warning(f'Watchdog screenshot saved: {file}')
        except Exception as e:
            logger.error(f'Save watchdog screenshot failed: {e}')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.run()
