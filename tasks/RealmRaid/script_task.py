# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
import re
from cached_property import cached_property
from module.base.timer import Timer
from tasks.GameUi.default_pages import page_exploration, random_click

from tasks.base_task import BaseTask
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralBattle.general_battle import BattleAction, BattleContext, ExitMatcher, GeneralBattle
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_realm_raid
from tasks.RealmRaid.assets import RealmRaidAssets
from tasks.RealmRaid.config import RealmRaid, AttackNumber, WhenAttackFail
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.RealmRaid.page import page_shikigami_records


from module.logger import logger
from module.exception import TaskEnd
from module.atom.image_grid import ImageGrid
from module.atom.image import RuleImage
from module.atom.click import RuleClick


class ScriptTask(GeneralBattle, GameUi, SwitchSoul, RealmRaidAssets):
    medal_grid: ImageGrid = None
    init_tickets: int = -1

    def _handle_result(self, context: BattleContext, config: GeneralBattleConfig) -> BattleAction:
        if config.quick_exit:
            context.reward_no_battle_ts = None
            context.is_win = not self.appear(self.I_FALSE)
            return BattleAction.EXIT_WIN if context.is_win else BattleAction.EXIT_LOSE
        return super()._handle_result(context, config)

    def _exit_matcher(self) -> ExitMatcher:
        return self.I_BACK_RED

    def run(self):
        con = self.config.realm_raid
        # 直接进入个人突破页面
        self.goto_page(page_realm_raid)

        # 在突破页面内先判断票数，如果没有票了或者已经达到攻击次数上限，就直接结束任务
        if not self.check_ticket(con.raid_config.number_base):
            self.goto_page(page_exploration)
            self.set_next_run(task='RealmRaid', success=False, finish=True)
            raise TaskEnd

        # 票数足够，现在开始进行御魂切换
        if con.switch_soul_config.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(con.switch_soul_config.switch_group_team)
                
        if con.switch_soul_config.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(con.switch_soul_config.group_name, con.switch_soul_config.team_name)
            
        # 切换完成后，必须返回突破页面
        self.goto_page(page_realm_raid)

        # 有呱太活动的时候第一次进入还会 出现一个弹窗
        self.screenshot()
        if self.appear(self.I_FROG_RAID):
            logger.info(f'Click {self.I_FROG_RAID.name}')
            while 1:
                self.screenshot()
                if not self.appear(self.I_FROG_RAID):
                    break
                if self.appear_then_click(self.I_FROG_RAID, interval=1):
                    continue
        # 判断是不是锁定阵容
        self.ensure_lock(con.general_battle_config.lock_team_enable)
        # 判断是否是呱太活动
        frog = self.is_frog(True)
        if frog:
            logger.info(f'Frog raid')

        # 开始循环
        success = True
        last_battle = True  # 记录上一次战斗的结果
        # 更改循环顺序
        while 1:
            self.screenshot()
            # 检查票数
            if not self.check_ticket(con.raid_config.number_base):
                break
            # ----------------------------------------开始进攻
            medal, index = self.find_one(False)
            if not medal and not index:
                # 已经没有可以挑战的了，只能刷新
                if con.raid_config.when_attack_fail == WhenAttackFail.CONTINUE:
                    logger.info('No one can attack and then refresh')
                    # 刷新列表: 若刷新按钮 CD 中, 等待 CD 结束再刷; 刷新后仍无目标则重试, 超过上限直接退出
                    if self.refresh_with_cd_wait(max_tries=2):
                        continue
                    else:
                        logger.warning('No target available after refresh retries, exit realm raid')
                        success = False
                        break
                else:
                    logger.info('No one can attack, break')
                    success = False
                    break
            # 判断是不是左上角第一个
            lock_before = con.general_battle_config.lock_team_enable
            handled_first_target = False
            if index == 1:
                logger.info('Now is the first one')
                if con.raid_config.exit_four:
                    logger.info('Exit four enable')
                    if not self.fire(index):
                        # 没有成功进入战斗则重新检查票数和其他条件
                        continue
                    self.run_general_battle(config=self.build_quick_exit_config(con.general_battle_config))
                    self.fire_again()
                    self.run_general_battle(config=self.build_quick_exit_config(con.general_battle_config))
                    self.fire_again()
                    self.run_general_battle(config=self.build_quick_exit_config(con.general_battle_config))
                    self.fire_again()
                    self.run_general_battle(config=self.build_quick_exit_config(con.general_battle_config))
                    self.fire_again()
                    last_battle = self.run_general_battle(con.general_battle_config)
                    handled_first_target = True
            elif self.check_medal_is_frog(frog, medal, index):
                # 如果挑战的这只是呱太的话，就要把锁定改为不锁定
                con.general_battle_config.lock_team_enable = False
            if not handled_first_target:
                if not self.fire(index):
                    # 没有成功进入战斗则重新检查票数和其他条件
                    continue
                last_battle = self.run_general_battle(con.general_battle_config)
            if lock_before:
                con.general_battle_config.lock_team_enable = lock_before
            # 检查是否每三次领一个奖励
            if self.reward_detect_click(False):
                logger.info('Rewards of three wins')
                continue
            # 刷新 >> 如果勾选了三次刷新并且到达了三次，就刷新
            if con.raid_config.three_refresh and self.appear(self.I_RR_THREE, threshold=0.8):
                logger.info('Three refresh')
                if self.check_refresh():
                    continue
                else:
                    success = False
                    break
            # 刷新 >> 如果上一轮的失败并且勾选了失败刷新，就刷新
            if not last_battle and con.raid_config.when_attack_fail == WhenAttackFail.REFRESH:
                logger.info('Battle lost and then refresh')
                if self.check_refresh():
                    continue
                else:
                    success = False
                    break
            # 如果上一轮失败 -> 退出
            if not last_battle and con.raid_config.when_attack_fail == WhenAttackFail.EXIT:
                logger.info('Battle lost and exit')
                break
            # 降级 >> 独立开关: 战斗失败(非退4的"退")后, 找一个人反复快速退出
            # downgrade_count 次降低突破等级, 再刷新列表让后续对手更弱
            # 注意: 退4分支(exit_four)的快速退出不写入 last_battle, 不会误触发;
            # 仅真实战斗失败(last_battle=False)才进入, 与上面 REFRESH/EXIT 分支互不影响
            if not last_battle and con.raid_config.downgrade_enable:
                logger.info('Battle lost and downgrade')
                if self._downgrade(con):
                    continue
                else:
                    # 降级失败只结束本轮, 不标记任务失败(8-04修复: 降级收尾"没领到三胜奖励"
                    # 曾被误判为"回界面失败"→ success=False 把 next_run 推到明天, 刷新区块
                    # 根本没执行; 与上方 EXIT 分支(159行)同款处理——降级/战斗收尾问题不应背
                    # "任务失败"惩罚, 下次按正常调度重跑)
                    break

        self.goto_page(page_exploration)
        self.set_next_run(task='RealmRaid', success=success, finish=True)
        raise TaskEnd

    def is_ticket(self) -> bool:
        """
        如果没有票了，那么就返回False
        :return:
        """
        self.wait_until_appear(self.I_BACK_RED)
        self.screenshot()
        cu, res, total = self.O_NUMBER.ocr(self.device.image)
        if cu == 0 and cu + res == total:
            logger.warning(f'Execute round failed, no ticket')
            return False
        return True

    def medal_fire(self) -> bool:
        """
        点击勋章
        :return:
        """
        # 点击勋章的挑战 和挑战
        time.sleep(0.2)
        is_click = False
        while 1:
            self.screenshot()

            if self.appear(self.I_FIRE, threshold=0.8):
                break

            if self.appear_then_click(self.I_SOUL_RAID, interval=1.5):
                while 1:
                    self.screenshot()
                    if self.appear_then_click(self.I_SOUL_RAID, interval=1.5):
                        continue
                    if not self.appear(self.I_SOUL_RAID, threshold=0.6):
                        break
                continue

            target = self.medal_grid.find_anyone(self.device.image, frame_id=self.device.image_frame_id)
            if target:
                self.appear_then_click(target, interval=2)  # 点击勋章,但是设置为两秒的间隔，适应不同的模拟器速度
                is_click = not is_click

            if is_click:
                continue
        logger.info(f'Click Medal')

        # 点击挑战
        self.wait_until_appear(self.I_FIRE)
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_FIRE, interval=2):
                continue
            if not self.appear(self.I_FIRE, threshold=0.8):
                break
        logger.info(f'Click {self.I_FIRE.name}')

    # ----------------------------------------------------------------------------------------------------------------------
    # 2023.7.21 改版个人突破
    def ensure_lock(self, lock_team_enable: bool):
        """
        确保锁定阵容
        :param lock_team_enable:
        :return:
        """
        if lock_team_enable:
            while 1:
                self.screenshot()
                if self.appear_then_click(self.I_UNLOCK, interval=1):
                    continue
                if self.appear_then_click(self.I_UNLOCK_2, interval=1):
                    continue
                if self.appear(self.I_LOCK_2, threshold=0.9):
                    break
                if self.appear(self.I_LOCK, threshold=0.9):
                    break
            logger.info(f'Click {self.I_UNLOCK.name}')
        else:
            while 1:
                self.screenshot()
                if self.appear_then_click(self.I_LOCK, interval=1):
                    continue
                if self.appear_then_click(self.I_LOCK_2, interval=1):
                    continue
                if self.appear(self.I_UNLOCK_2, threshold=0.9):
                    break
                if self.appear(self.I_UNLOCK, threshold=0.9):
                    break
            logger.info(f'Click {self.I_LOCK.name}')

    def is_frog(self, screenshot: bool=True) -> bool:
        """
        判断是不是呱太活动
        :return:
        """
        if screenshot:
            self.screenshot()
        if self.appear(self.I_FROG_MEDAL):
            return True
        return False

    def check_ticket(self, base: int=0) -> bool:
        """
        检查是不是有票， 检查这个票是否大于等于基准
        :param base:
        :return:
        """
        if base < 0 or base > 30:
            logger.warning(f'It is not a valid base {base}')
            base = 0
        self.wait_until_appear(self.I_BACK_RED)
        self.screenshot()
        cu, res, total = self.O_NUMBER.ocr(self.device.image)

        if total == 0:
            self.reward_detect_click(True)
            # 增加出现聊天框遮挡，处理奖励之后，重新识别票数
            cu, res, total = self.O_NUMBER.ocr(self.device.image)
        if cu == 0 and cu + res == total:
            logger.warning(f'Execute raid failed, no ticket')
            return False
        elif cu + res == total and cu < base:
            logger.warning(f'Execute raid failed, ticket is not enough')
            return False
        self.init_tickets = cu if self.init_tickets == -1 else self.init_tickets
        if self.init_tickets - cu >= self.config.realm_raid.raid_config.number_attack:  # 检查挑战次数
            logger.info(f'Current count {self.init_tickets - cu}, '
                        f'max count {self.config.realm_raid.raid_config.number_attack}')
            return False
        return True

    @cached_property
    def order_medal(self) -> ImageGrid:
        order_attack = self.config.realm_raid.raid_config.order_attack
        support_number = [0, 1, 2, 3, 4, 5]
        match = {
            0: self.I_MEDAL_0,
            1: self.I_MEDAL_1,
            2: self.I_MEDAL_2,
            3: self.I_MEDAL_3,
            4: self.I_MEDAL_4,
            5: self.I_MEDAL_5,
        }
        order = order_attack.replace(' ', '').replace('\n', '')
        order = re.split(r'>', order)
        order = [int(i) for i in order]
        order = [i for i in order if i in support_number]

        images = []
        for i in order:
            images.append(match[i])
        return ImageGrid(images)

    @cached_property
    def partition(self) -> list[RuleClick]:
        return [self.C_PARTITION_1, self.C_PARTITION_2, self.C_PARTITION_3, self.C_PARTITION_4, self.C_PARTITION_5,
                self.C_PARTITION_6, self.C_PARTITION_7, self.C_PARTITION_8, self.C_PARTITION_9]

    def find_one(self, screenshot: bool=True) -> tuple:
        """
        找到一个可以打的，并且检查一下是不是这一个的是第几个的
        我们约定次序是：从左到右 上到下
        1 2 3
        4 5 9
        7 8 9
        :return: 返回的第一个参数是一个RuleImage, 第二个参数是位置信息
        如果没有找到，返回None, None
        """
        if screenshot:
            self.screenshot()
        image = self.device.image
        # https://github.com/runhey/OnmyojiAutoScript/issues/71
        # 如果开始失败后继挑战剩下的
        if self.config.realm_raid.raid_config.when_attack_fail == WhenAttackFail.CONTINUE:
            for i, roi in enumerate(self.false_roi):
                self.false_image.roi_back = roi
                if not self.appear(self.false_image):
                    continue
                logger.info(f'Position {i+1} is a failed')
                x, y, w, h = self.partition[i].roi_back
                image[y:y+h, x:x+w, ...] = 0
        # -----------------------------------------------------
        target = self.order_medal.find_anyone(image)
        if target:
            center = target.front_center()
            for i, click in enumerate(self.partition):
                x1, x2, y1, y2 = click.roi_front[0], click.roi_front[0] + click.roi_front[2], \
                                 click.roi_front[1], click.roi_front[1] + click.roi_front[3]
                if x1 < center[0] < x2 and y1 < center[1] < y2:
                    logger.info(f'Find one medal [{target}], order is {i + 1}')
                    return target, i + 1

        return None, None

    def check_medal_is_frog(self, is_activity: False, target: RuleImage, order: int) -> bool:
        """
        检查这个是不是呱太，为此之前你还需要判断是不是 处于呱太活动的
        :param target:
        :param is_activity: 如果不是呱太活动，那么就不需要检查了
        :param order:
        :return:
        """
        if not is_activity:
            return False
        # 好像呱太的位置是只有 789这三个
        if order < 7:
            return False
        # 有时候四星可能和五星的混一起
        if target != self.I_MEDAL_5 and target != self.I_MEDAL_4:
            return False
        match_ocr = {
            1: self.O_FROG_1,
            2: self.O_FROG_2,
            3: self.O_FROG_3,
            4: self.O_FROG_4,
            5: self.O_FROG_5,
            6: self.O_FROG_6,
            7: self.O_FROG_7,
            8: self.O_FROG_8,
            9: self.O_FROG_9,
        }
        target_ocr = match_ocr[order]
        self.screenshot()
        if target_ocr.ocr(self.device.image) == 20:
            logger.info(f'Find frog medal [{target}]')
            return True
        return False

    def reward_detect_click(self, screenshot: bool=True) -> bool:
        """
        检测是否出现 每三次就有奖励的界面, 有就领取
        :return:
        """
        if screenshot:
            self.screenshot()
        # 由于更改识别顺序，退出战斗之后，需要先等待回到个人突破界面，即识别到红色退出按钮，再进行奖励判断
        # 多轮重试: 结算页Lose后可能停在结算残影/半透明过渡态, I_BACK_RED迟迟不出现
        # (8-02实测: 第12场Lose后 09:07:54 -> 09:08:51 Wait too long, 60s空集卡死重启)
        # 顺序注意: 必须先等待再点击。正常流程(已回到突破界面)下 wait_until_appear
        # 立即命中 -> 零多余点击, 与原版行为一致; 只有等不到返回键(卡在结算过渡态)
        # 才随机点击推进页面过渡。多轮(3x6s+2次点击≈20s, 低于60s卡死保护)
        # 仍检测不到才导航回突破界面自救, 避免无限等待触发卡死保护
        back_waited = False
        for _ in range(3):
            if self.wait_until_appear(self.I_BACK_RED, wait_time=6):
                back_waited = True
                break
            self.click(random_click(), interval=0.8)
        if not back_waited:
            logger.warning('Back to realm raid after battle timeout, goto realm raid to recover')
            self.goto_page(page_realm_raid)
            return False
        self.ui_click_until_disappear(self.I_SOUL_RAID, interval=1.2)
        text = self.O_TEXT.ocr(self.device.image)
        # 识别突破卷区域，如果识别到了且其中含有文字，即有聊天框遮挡则进入循环，等待三胜奖励出现并点击，循环退出条件为识别到票（即*/*的形式）
        if text != "" and re.search(r'[\u4e00-\u9fff]', text):
            while 1:
                self.screenshot()
                result = self.O_TEXT.ocr(self.device.image)
                if not re.search(r'[\u4e00-\u9fff]', result) and re.search(r'(\d+)/(\d+)', result):
                    return True
                if self.appear_then_click(self.I_SOUL_RAID, interval=1.5):
                    continue
        return False

    def check_refresh(self, screenshot: bool=True) -> bool:
        """
        检查是否出现了刷新的按钮
        如果可以刷新就刷新，返回True
        如果在CD中，就返回False
        :return:
        """
        if screenshot:
            self.screenshot()
        if not self.appear(self.I_FRESH):
            logger.info(f'No find refresh button and it is in CD')
            return False
        # 等待确认弹窗出现，带超时保护防止刷新动画异常时无限循环
        ensure_timer = Timer(5)
        ensure_timer.start()
        while 1:
            self.screenshot()
            if self.appear(self.I_FRESH_ENSURE):
                break
            if self.appear_then_click(self.I_FRESH, interval=1):
                continue
            if ensure_timer.reached():
                logger.warning('Refresh confirm window not appear within 5s, give up refresh')
                return False
        # 等待确认弹窗消失，带超时保护
        done_timer = Timer(5)
        done_timer.start()
        while 1:
            self.screenshot()
            if not self.appear(self.I_FRESH_ENSURE):
                # 刷新动画未完全结束就扫描容易误判"无目标可打", 等待动画稳定后再返回
                time.sleep(2)
                return True
            if self.appear_then_click(self.I_FRESH_ENSURE, interval=1):
                continue
            if done_timer.reached():
                logger.warning('Refresh confirm window not disappear within 5s, give up refresh')
                return False
        return False

    def refresh_with_cd_wait(self, max_tries: int = 2, cd_wait_limit: int = 200) -> bool:
        """
        无可打目标时的刷新逻辑: 若刷新按钮 CD 中则等待 CD 结束再刷, 刷新后重新扫描。
        仍无目标则重试, 超过 max_tries 次直接返回 False 让任务优雅退出。

        Args:
            max_tries: 最大刷新尝试次数（含 CD 等待后的再次刷新）。
            cd_wait_limit: 单次等待刷新 CD 的最长秒数（刷新 CD 为 3 分钟, 默认 200s 留有余量）。

        Returns:
            bool: 刷新后找到可打目标返回 True, 否则 False。
        """
        for attempt in range(1, max_tries + 1):
            logger.info(f'Refresh attempt {attempt}/{max_tries}')
            # 先确保刷新按钮可用: CD 中则等待 CD 结束
            self.screenshot()
            if not self.appear(self.I_FRESH):
                logger.info(f'Refresh button in CD, waiting (attempt {attempt}/{max_tries})')
                if not self.wait_fresh_cd(timeout=cd_wait_limit):
                    return False
            # 按钮可用后点击刷新
            if not self.check_refresh():
                logger.warning(f'Refresh attempt {attempt} failed')
                continue
            # 刷新成功, 动画等待已在 check_refresh 内完成, 重新扫描
            self.screenshot()
            medal, index = self.find_one(False)
            if medal and index:
                return True
            logger.info(f'Refresh attempt {attempt} done but still no target')
        return False

    def wait_fresh_cd(self, timeout: int = 200) -> bool:
        """
        等待刷新按钮 CD 结束。不依赖 OCR 读剩余时间(容易误读), 直接按固定节奏
        轮询刷新按钮是否恢复可用(模板匹配, 与 check_refresh 同一判断依据)。

        注意: 等待可能超过设备层 60s 普通卡死保护, 必须挂 PAUSE 长等待标记
        (stuck_long_wait_list 内, 放宽到 300s), 结束时清除。

        Args:
            timeout: 最长等待秒数(刷新 CD 为 3 分钟, 默认 200s 留有余量)。

        Returns:
            bool: CD 结束后刷新按钮可用返回 True, 超时返回 False。
        """
        self.device.stuck_record_clear()
        self.device.stuck_record_add('PAUSE')
        try:
            timer = Timer(timeout)
            timer.start()
            while 1:
                if timer.reached():
                    logger.warning(f'Wait refresh CD timeout after {timeout}s')
                    return False
                # 低频轮询: 降低截图频率避免触发卡死检测, 也能在按钮提前恢复时及时感知
                time.sleep(15)
                self.screenshot()
                if self.appear(self.I_FRESH):
                    logger.info('Refresh button available after CD')
                    return True
        finally:
            self.device.stuck_record_clear()

    def fire(self, order: int) -> bool:
        """
        挑战
        :param order:  第几个
        :return: 是否点击进攻成功
        """
        click = self.partition[order - 1]
        self.wait_until_appear(self.I_RR_PERSON)
        self.device.click_record_clear()
        while True:
            self.screenshot()
            if not self.appear(self.I_RR_PERSON):
                return True
            if self.appear_then_click(self.I_FIRE, interval=1):
                continue
            if self.click(click, interval=2):
                continue
        logger.info(f'Click fire {order} success')
        return False

    def fire_again(self) -> bool:
        """
        失败界面再次挑战
        :return: 是否再战成功
        """
        self.wait_until_appear(self.I_FIRE_AGAIN)
        while True:
            self.screenshot()
            if not self.appear(self.I_FIRE_AGAIN):
                logger.info(f'Click fire again success')
                return True
            if self.appear_then_click(self.I_SHOW_AGAIN, interval=2):
                continue
            if self.appear_then_click(self.I_FRESH_ENSURE, interval=2):
                continue
            if self.appear_then_click(self.I_FIRE_AGAIN, interval=2):
                continue
        return False

    def _downgrade(self, con: RealmRaid) -> bool:
        """
        降级: 战斗失败后, 找一个目标反复"快速退出" downgrade_count 次, 降低突破等级,
        然后刷新列表, 让后续对手更弱。完全复用退4(exit_four)的快速退出机制:
        build_quick_exit_config + fire_again, 纯新增方法不影响任何现有逻辑。

        流程:
          1. find_one 找一个可打目标
          2. fire(index) 进入该目标战斗
          3. 循环 downgrade_count-1 次: run_general_battle(quick_exit) 快速退出(判负)
             + fire_again 失败界面再战, 回到准备页
          4. 最后一次 run_general_battle(quick_exit) 快速退出(判负), 停在失败界面
          5. 尝试回突破界面: 复用 reward_detect_click 的返回键等待+点击推进+导航自救,
             但不依赖其返回值(语义是"是否领到三胜奖励", 与"是否回到界面"无关),
             随后自己截图确认 I_BACK_RED, 不在则 goto_page 兜底
          6. refresh_with_cd_wait 刷新列表(带 CD 等待), 刷新成功返回 True

        :param con: RealmRaid 配置对象
        :return: True=降级+刷新成功, False=仅"刷新列表失败"才返回(回界面问题已被
                 第5步兜底吸收, 不再因领奖/回界面问题整体判失败)
        """
        # 1. 找目标(复用主循环同款 find_one, 找当前列表第一个可打的)
        medal, index = self.find_one(False)
        if not medal or not index:
            logger.warning('Downgrade: no target found, skip')
            return False
        # 2. 进入该目标战斗(消耗1张突破票)
        if not self.fire(index):
            logger.warning('Downgrade: fail to enter battle, skip')
            return False
        # 3+4. 反复快速退出 downgrade_count 次 (前 N-1 次退完再战, 最后一次退完停失败界面)
        for _ in range(con.raid_config.downgrade_count - 1):
            self.run_general_battle(config=self.build_quick_exit_config(con.general_battle_config))
            self.fire_again()
        self.run_general_battle(config=self.build_quick_exit_config(con.general_battle_config))
        logger.info(f'Downgrade done, total quick-exit {con.raid_config.downgrade_count} times')
        # 5. 从失败界面回到突破界面。
        # 8-04修复(核心): reward_detect_click 的返回值语义是"是否领到三胜奖励",
        # 不是"是否回到突破界面"。8-04 13:54 实测: 页面已回突破界面(I_BACK_RED 第2轮命中、
        # OCR 票数 18/30), 只因无三胜奖励返回 False, 被旧代码误判为"回界面失败"直接结束
        # 任务, 第6步刷新根本没执行, next_run 被推到明天。
        # 修复: 调用后不依赖返回值, 自己截图确认 I_BACK_RED 在不在, 不在才 goto_page 兜底。
        # (reward_detect_click 内部本身就有"3轮等不到→goto_page 导航自救"的分支, 此处是
        # 双保险: 导航后再确认一次, 仍不在才再导航)
        self.reward_detect_click(False)
        self.screenshot()
        if not self.appear(self.I_BACK_RED):
            logger.warning('Downgrade: not back to realm raid after reward detect, goto realm raid')
            self.goto_page(page_realm_raid)
        # 6. 刷新列表继续打(刷新是降级闭环的最后一步, 只有刷新失败才真正判降级失败)
        self.screenshot()
        if not self.refresh_with_cd_wait(max_tries=2):
            logger.warning('Downgrade: fail to refresh list, skip')
            return False
        return True

    @cached_property
    def false_roi(self) -> list:
        width = 86
        height = 64
        x1 = 386
        x2 = 714
        x3 = 1047
        y1 = 143
        y2 = 277
        y3 = 414
        return [
            [x1, y1, width, height],  # 左上角
            [x2, y1, width, height],
            [x3, y1, width, height],
            [x1, y2, width, height],  # 左中
            [x2, y2, width, height],
            [x3, y2, width, height],
            [x1, y3, width, height],  # 左下
            [x2, y3, width, height],
            [x3, y3, width, height],
        ]

    @cached_property
    def false_image(self):
        return RuleImage(roi_front=(0 ,0, 63, 32),
                         roi_back=(0, 0, 100, 100),
                         threshold=0.8,
                         method="Template matching",
                         file="./tasks/RyouToppa/dev/loser_sign_1.png")


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device
    config = Config('oas1')
    device = Device(config)
    t = ScriptTask(config, device)

    t.run()
