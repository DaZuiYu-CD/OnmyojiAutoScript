# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time

from module.base.timer import Timer
from module.exception import RequestHumanTakeover, GameTooManyClickError, GameStuckError
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.Restart.assets import RestartAssets
from tasks.base_task import BaseTask


class LoginService(BaseTask, RestartAssets, GameUiAssets):
    character: str

    def __init__(self, *wargs, **kwargs):
        super().__init__(*wargs, **kwargs)
        self.character = self.config.restart.login_character_config.character
        self.O_LOGIN_SPECIFIC_SERVE.keyword = self.character

    def _app_handle_login(self) -> bool:
        """
        最终是在庭院界面
        :return:
        """
        logger.hr('App login')
        self.device.stuck_record_add('LOGIN_CHECK')

        confirm_timer = Timer(1.5, count=2).start()
        orientation_timer = Timer(10)
        skip_login_animation = True
        login_success = False

        while 1:
            if not login_success and orientation_timer.reached():
                self.device.get_orientation()
                orientation_timer.reset()

            self.screenshot()
            if self.appear_then_click(self.I_CANCEL_BATTLE, interval=0.8):
                logger.info('Cancel continue battle')
                continue
            if self.appear(self.I_CHECK_MAIN, interval=0.2) and not self.appear(self.I_MAIN_GOTO_SHIKIGAMI_RECORDS):
                logger.info('The main had already appeared, but shikigami records had not yet appeared')
                if self.click(self.C_LOGIN_SCROLL_CLOSE_AREA, interval=2):
                    continue
            if self.appear(self.I_MAIN_GOTO_SHIKIGAMI_RECORDS, interval=0.2):
                if confirm_timer.reached():
                    logger.info('Login to main confirm (shikigami records button appears)')
                    break
            else:
                confirm_timer.reset()
            if self.appear(self.I_MAIN_GOTO_SHIKIGAMI_RECORDS, interval=0.5):
                logger.info('Login success: shikigami records button appears')
                login_success = True
            if self.appear(self.I_HARVEST_ZIDU, interval=1):
                self.I_HARVEST_ZIDU.roi_front[0] -= 200
                self.I_HARVEST_ZIDU.roi_front[1] -= 200
                if self.click(self.I_HARVEST_ZIDU, interval=2):
                    logger.info('Close zidu')
                continue
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=2.5):
                logger.info('Soul overflow confirm')
                continue
            if self.appear_then_click(self.I_LOGIN_LOAD_DOWN, interval=1):
                logger.info('Download inbetweening')
                continue
            if self.appear_then_click(self.I_WATCH_VIDEO_CANCEL, interval=0.6):
                logger.info('Close video')
                continue
            if self.appear_then_click(self.I_LOGIN_RED_CLOSE, interval=0.6):
                logger.info('Close red close')
                continue
            if self.appear_then_click(self.I_LOGIN_YELLOW_CLOSE, interval=0.6):
                logger.info('Close yellow close')
                continue
            if self.appear_then_click(self.I_LOGIN_LOGIN_GOTO_BIND_PHONE):
                while 1:
                    self.screenshot()
                    if self.appear_then_click(self.I_LOGIN_LOGIN_CANCEL_BIND_PHONE):
                        logger.info("Close bind phone")
                        break
                continue
            from tasks.Component.GeneralInvite.assets import GeneralInviteAssets as gia
            if self.appear_then_click(gia.I_I_REJECT, interval=0.8):
                logger.info("reject invites")
                continue
            if self.appear_then_click(self.I_LOGIN_LOGIN_ONMYOJI_GENIE):
                logger.info("click onmyoji genie")
                continue
            if self.appear(self.I_LOGIN_SPECIFIC_SERVE, interval=0.6) \
                    and self.ocr_appear_click(self.O_LOGIN_SPECIFIC_SERVE, interval=0.6):
                while True:
                    self.screenshot()
                    if self.appear(self.I_LOGIN_SPECIFIC_SERVE):
                        self.click(self.C_LOGIN_ENSURE_LOGIN_CHARACTER_IN_SAME_SVR, interval=2)
                        continue
                    break
                logger.info('login specific user')
                continue

            if self.appear(self.I_CREATE_ACCOUNT):
                logger.warning('Appear create account')
                raise GameStuckError('Appear create account')
            if self.appear(self.I_CHARACTARS, interval=1):
                logger.info('误入区服设置')
                self.device.click(x=106, y=535)
                continue
            if self.appear(self.I_EARLY_SERVER) and self.appear_then_click(self.I_EARLY_SERVER_CANCEL):
                logger.info('Cancel switch from early server to normal server')
                continue

            if self.appear(self.I_LOGIN_8, interval=0.6): # 进入登录页面后不再处理登录动画逻辑
                skip_login_animation = False
            if skip_login_animation:
                if self.ocr_appear_click(self.O_LOGIN_ANIMATION_SKIP, interval=2.5):  # 点击跳过登录动画
                    continue
                self.click(self.C_LOGIN_ANIMATION_CENTER, interval=5)  # 每5秒点击一次屏幕中央

            if self.ocr_appear_click(self.O_LOGIN_ENTER_GAME, interval=3):
                skip_login_animation = False  # 进入登录页面后不再处理登录动画逻辑
                self.wait_until_appear(self.I_LOGIN_SPECIFIC_SERVE, True, wait_time=5)
                continue

        return login_success

    def app_handle_login(self) -> bool:
        """
        登录后等待进入庭院(点"进入游戏"→ 等庭院特征 I_MAIN_GOTO_SHIKIGAMI_RECORDS 出现)。
        点"进入游戏"后长时间未识别到庭院(GameStuckError/TooManyClick, 设备层 60s 卡死保护触发)
        → 重启游戏重新登录, 最多重试 2 次; 2 次仍失败 → 抛 RequestHumanTakeover。
        2026-08-13 改造(8-10 实测根因): 原逻辑"重启游戏 1 次后立即抛人工接管", 且重启后不再等待,
        导致 AccountDaily 切号登录时一次偶发加载卡死(如回归号 60s 未进庭院)就整批账号全灭。
        现在给 2 次机会(重启游戏→重新点进入游戏→再等), 偶发慢加载/回归号能恢复;
        2 次仍失败才抛人工接管, 由调用方决定跳过该账号或人工介入(AccountDaily 捕获后跳过账号)。
        """
        self.device.stuck_record_clear()
        self.device.click_record_clear()
        max_retry = 2
        for attempt in range(1, max_retry + 1):
            try:
                self._app_handle_login()
                return True
            except (GameTooManyClickError, GameStuckError) as e:
                logger.warning('Login wait failed attempt %d/%d: %s', attempt, max_retry, e)
                if attempt < max_retry:
                    logger.info('Restart game and retry login wait')
                    self.device.app_stop()
                    self.device.app_start()
                    time.sleep(5)
                # 重试前清空卡死/点击记录, 避免上次失败残留影响下一次判定
                self.device.stuck_record_clear()
                self.device.click_record_clear()

        logger.critical('Login failed: 点"进入游戏"后 %d 次未识别到庭院(已重启游戏重试)', max_retry)
        logger.critical('Onmyoji server may be under maintenance, or you may lost network connection')
        raise RequestHumanTakeover

    def set_specific_usr(self, character: str):
        self.character = character
        self.O_LOGIN_SPECIFIC_SERVE.keyword = character
