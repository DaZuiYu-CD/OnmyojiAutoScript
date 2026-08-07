# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
from time import sleep

from enum import Enum
from cached_property import cached_property
from datetime import datetime, timedelta

from module.logger import logger
from module.exception import TaskEnd
from module.base.timer import Timer

from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.DemonEncounter.config import BossType, DemonEncounter, convert_to_general_battle_config
from tasks.DemonEncounter.page import page_rwt
from tasks.GameUi.default_pages import page_main, random_click
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_shikigami_records
from tasks.DemonEncounter.assets import DemonEncounterAssets
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.DemonEncounter.data.answer import Answer


class LanternClass(Enum):
    BATTLE = 0  # 打怪  --> 无法判断因为怪的图片不一样，用排除法
    BOX = 1  # 开宝箱
    MAIL = 2  # 邮件答题
    REALM = 3  # 打结界
    EMPTY = 4  # 空
    MYSTERY = 5  # 神秘任务
    BOSS = 6  # 大鬼王


class ScriptTask(GameUi, GeneralBattle, DemonEncounterAssets, SwitchSoul):
    conf: DemonEncounter = None

    def run(self):
        self.conf = self.config.demon_encounter
        if not self.check_time():
            logger.warning('Time is not right')
            raise TaskEnd('DemonEncounter')
        # 切换御魂
        soul_config = self.config.demon_encounter.demon_soul_config
        best_soul_config = self.config.demon_encounter.best_demon_soul_config
        if soul_config.enable or best_soul_config.enable:
            self.goto_page(page_shikigami_records)
            self.checkout_soul()
        self.goto_page(page_rwt)
        self.execute_lantern()
        self.execute_boss()
        self.goto_page(page_main)
        self.set_next_run(task='DemonEncounter', success=True, finish=False)
        raise TaskEnd('DemonEncounter')

    def checkout_soul(self):
        """
        切换御魂
        """
        select_best_demon = getattr(self.conf.best_demon_boss_config, f'{self.boss_type}_select', False)
        if select_best_demon:
            group, team = getattr(self.conf.best_demon_soul_config, self.boss_type).split(",")
        else:
            group, team = getattr(self.conf.demon_soul_config, self.boss_type).split(",")
        if group and team:
            self.run_switch_soul_by_name(group, team)
            return
        logger.error(f'Unknown switch soul conf: group[{group}], team[{team}]')

    def execute_boss(self):
        """
        打boss
        :return:
        """
        logger.hr('Start boss battle', 1)

        def find_boss():
            find_btn_clicked = False
            timer_find_boss = Timer(10 * 60)
            timer_find_boss.start()
            # 挂 PAUSE 长等待标记: find_boss 循环可能超过设备层 60s 普通卡死保护。
            # 8-02 实测: 首领按钮 ROI 偏移 2px 且 ROI=模板尺寸无滑动余地, 匹配 0.46
            # 识别不到 -> 永不点击 -> 60s 空集卡死 GameStuckError 重启 (老区号/小号6)。
            # 挂 PAUSE(stuck_long_wait_list 内)后保护放宽到 300s, 由内置 10 分钟
            # 超时优雅兜底(set_next_run + TaskEnd), 不再被 60s 卡死保护打爆。
            self.device.stuck_record_clear()
            self.device.stuck_record_add('PAUSE')
            try:
                while 1:
                    self.screenshot()
                    if self.appear(self.I_BOSS_FIRE) or self.appear(self.I_BEST_BOSS_FIRE):
                        break
                    if timer_find_boss.reached():
                        logger.warning('find boss timeout')
                        self.set_next_run(task='DemonEncounter', success=False, finish=True, server=False)
                        raise TaskEnd('DemonEncounter')
                    if self.appear(self.I_JADE_50):
                        # 没找到boss但地图中央出现宝箱，导致点击宝箱出现50勾玉购买界面
                        self.ui_click_until_smt_disappear(self.I_DE_FIND, self.I_JADE_50, interval=1)
                        continue
                    if find_btn_clicked and self.click(self.C_DM_BOSS_CLICK, interval=5):
                        find_btn_clicked = False
                        continue
                    if self.best_demon_enable:
                        self.device.click_record_clear()
                        if self.appear(self.I_DE_BOSS_BEST) and (not find_btn_clicked):
                            self.device.click_record_remove(self.I_DE_BOSS_BEST)
                            if self.click(self.I_DE_BOSS_BEST, interval=4):
                                logger.info("Finding best boss...")
                                find_btn_clicked = True
                            continue
                    else:
                        if self.appear(self.I_DE_BOSS) and (not find_btn_clicked):
                            self.device.click_record_remove(self.I_DE_BOSS)
                            if self.click(self.I_DE_BOSS, interval=4):
                                logger.info("Finding normal boss...")
                                find_btn_clicked = True
                            continue
                return True
            finally:
                self.device.stuck_record_clear()

        def enter_boss():
            logger.info('trying to enter boss...')
            # 点击集结挑战
            boss_fire_count = 0  # 三次没点到就意味着今天已经挑战过了
            ocr_people_item = self.O_DE_BEST_BOSS_PEOPLE if self.best_demon_enable else self.O_DE_BOSS_PEOPLE
            while 1:
                self.screenshot()

                if self.appear(self.I_BOSS_FIRE) or self.appear(self.I_BEST_BOSS_FIRE):
                    current, remain, total = ocr_people_item.ocr(self.device.image)
                    if total == 300 and current >= 290:
                        logger.info('Boss battle people is full')
                        if not self.appear(self.I_UI_BACK_RED):
                            logger.warning('Boss battle people is full but no red back')
                            continue
                        self.ui_click_until_disappear(self.I_UI_BACK_RED)
                        # 退出重新选一个没人慢的boss
                        logger.info('Exit and reselect')
                        return False

                logger.info('Boss battle people is not full')

                if self.appear(self.I_BOSS_CONFIRM):
                    self.ui_click(self.I_BOSS_NO_SELECT, self.I_BOSS_SELECTED)
                    self.ui_click(self.I_BOSS_CONFIRM, self.I_BOSS_GATHER)
                    break
                if self.appear(self.I_BOSS_GATHER):
                    break
                if boss_fire_count >= 3:
                    logger.warning('Boss battle already done')
                    self.set_next_run(task='DemonEncounter', success=False, finish=True, server=True)
                    self.ui_click_until_disappear(self.I_UI_BACK_RED)
                    raise TaskEnd('DemonEncounter')

                if (self.appear_then_click(self.I_BOSS_FIRE, interval=3)
                        or self.appear_then_click(self.I_BEST_BOSS_FIRE, interval=3)):
                    boss_fire_count += 1
                    continue
            return True

        fail_count = 0
        while True:
            if fail_count >= 5:
                return
            if not find_boss():
                continue
            if enter_boss():
                break
            fail_count += 1

        logger.info('Boss battle confirm and enter')
        self.device.stuck_record_clear()
        # 等待挑战, 5秒也是等
        time.sleep(5)
        refresh_timer = Timer(280)
        # 8-05 修复: 原逻辑把 I_BOSS_DONE_CHECK 单独当作"boss 打完"判定, 但失败结算页
        # 上也匹配该图(8-03 沙湖小号2 实测 0.983) -> 失败后误判 done -> break 跳到
        # wait_until_appear(I_BOSS_GATHER) 无限等, 而失败结算页 I_BOSS_GATHER 只有 0.032
        # 永不出现 -> 60s 空集卡死 GameStuckError 重启。
        # 修复: 1) done 判定改为 I_BOSS_DONE_CHECK 且 I_BOSS_GATHER/I_DE_LOCATION 双确认
        # (真回到主界面才算 done); 2) 新增失败结算页清理: 识别到 done_check 但不在主界面
        # 时, 点左上角白色退出键(I_BOSS_BACK_WHITE 实测 0.923)推进结算, 随机点击兜底,
        # 点掉后继续等主界面 —— 失败与胜利同样处理, 不按异常处理。
        settle_click = 0  # 结算页连续点击计数, 防止误判死循环
        while True:
            self.screenshot()
            done_check = self.appear(self.I_BOSS_DONE_CHECK)
            back_home = self.appear(self.I_BOSS_GATHER) or self.appear(self.I_DE_LOCATION)
            if done_check and back_home:
                # 8-05 补: 清理分支可能已挂 PAUSE(下面 done_check 分支), break 前必须清掉,
                # 否则 PAUSE 残留会把设备层卡死保护从 60s 放宽到 300s, 后续真卡死要多等 240s
                self.device.stuck_record_clear()
                break
            # 失败结算页清理: done_check 出现但不在主界面 -> 点退出推进结算
            # (胜利结算页 I_BOSS_WIN 匹配, 同样走这里点掉, 与失败一视同仁)
            if done_check and not back_home:
                # 挂 PAUSE 长等待标记: 结算页清理可能持续数秒, 不挂标记会以空集状态
                # 触发设备层 60s 普通卡死保护(8-03 沙湖小号2 就是空集卡死)。PAUSE 在
                # stuck_long_wait_list 内, 保护放宽到 300s, 足够点掉结算页回主界面。
                self.device.stuck_record_clear()
                self.device.stuck_record_add('PAUSE')
                if settle_click >= 6:
                    # 连续 6 次点击仍未离开结算页(退出键识别不到/点击无效): 不再硬点,
                    # 直接导航回封魔主界面兜底, 彻底避免无限空转。导航前清 PAUSE,
                    # 导航本身 10-30s 不会触发 60s 保护, 清掉避免标记残留
                    logger.warning('Dismiss battle settlement page failed after 6 clicks, goto page_rwt')
                    self.device.stuck_record_clear()
                    self.goto_page(page_rwt)
                    break
                elif self.appear_then_click(self.I_BOSS_BACK_WHITE, interval=1):
                    settle_click += 1
                    continue
                elif settle_click < 6:
                    # 退出键没识别到: 随机点击推进结算页 (与 run_general_battle 结算
                    # 处理同款, 限 6 次防死循环, 正常 1-3 次即点掉)
                    self.click(random_click(), interval=0.8)
                    settle_click += 1
                    continue
            if self.appear(self.I_BOSS_GATHER):
                # 8-05 补: 回到集结界面说明结算页已离开, 重置结算页点击计数,
                # 防止结算动画导致 DONE_CHECK 闪烁时计数残留误触发 6 次兜底
                settle_click = 0
                if not refresh_timer.started() or refresh_timer.reached():
                    self.device.stuck_record_clear()
                    self.device.stuck_record_add('BATTLE_STATUS_S')
                    logger.info('Boss Gathering...')
                    refresh_timer.reset()
                sleep(2)
                continue
            if self.appear(self.I_BOSS_WAIT):
                logger.info('Boss battle failed, waiting for 2 seconds...')
                self.device.stuck_record_clear()
                self.device.stuck_record_add('BATTLE_STATUS_S')
                refresh_timer.reset()
                sleep(2)
                continue
            if self.appear(self.I_PREPARE_HIGHLIGHT):
                if self.best_demon_enable:
                    general_battle_config = convert_to_general_battle_config(self.boss_type,
                                                                             best_demon_battle_conf=self.conf.best_demon_battle_config)
                else:
                    general_battle_config = convert_to_general_battle_config(self.boss_type,
                                                                             demon_battle_conf=self.conf.demon_battle_config)
                self.run_general_battle(config=general_battle_config, battle_key=self.boss_type)
                continue
            logger.info('Unknown scene Or Boss fight failed.waiting for Prepare_Button appear...')
            self.wait_until_appear(self.I_PREPARE_HIGHLIGHT, wait_time=2)

        # 等待回到挑战boss主界面
        # 8-05 修复: 原 wait_until_appear(I_BOSS_GATHER) 无超时无限等, 一旦上面 done 误判
        # 或结算页未点干净就 break 出来, 会永久卡死。加 15s 超时, 超时后 goto_page 导航兜底。
        if not self.wait_until_appear(self.I_BOSS_GATHER, wait_time=15):
            logger.warning('Wait back to boss main page timeout, goto page_rwt')
            self.goto_page(page_rwt)
        settle_back = Timer(15)
        settle_back.start()
        while 1:
            self.screenshot()
            if self.appear(self.I_DE_LOCATION):
                break
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1):
                settle_back.reset()
                continue
            if self.appear_then_click(self.I_BOSS_BACK_WHITE, interval=1):
                settle_back.reset()
                continue
            if settle_back.reached():
                logger.warning('Back to demon main page timeout, goto page_rwt')
                self.goto_page(page_rwt)
                break
        # 返回到封魔主界面

    def execute_lantern(self):
        """
        点灯笼 四次
        :return:
        """
        # 先点四次
        ocr_timer = Timer(0.8)
        ocr_timer.start()
        while 1:
            self.screenshot()
            if not ocr_timer.reached():
                continue
            else:
                ocr_timer.reset()
            cu, re, total = self.O_DE_COUNTER.ocr(self.device.image)
            if cu + re != total:
                logger.warning('Lantern count error')
                continue
            if cu == 0 and re == 4:
                break

            if self.appear_then_click(self.I_DE_FIND, interval=2.5):
                continue
        logger.info('Lantern count success')
        # 然后领取红色达摩
        self.screenshot()
        if not self.appear(self.I_DE_AWARD):
            self.ui_get_reward(self.I_DE_RED_DHARMA)
        self.wait_until_appear(self.I_DE_AWARD)
        # 8-05 新增: skip_lantern 开启时跳过四个灯笼的奖励处理, 直接打 boss。
        # 探查(上方循环)与红达摩领取是 boss 出现/奖励的前提, 保留; 只跳过
        # 灯笼的宝箱/答题/小怪/结界处理, 由 run() 里的 execute_boss 直接找 boss。
        # 注意用双层 getattr 防御: ①skip_lantern 放在嵌套模型 lantern_config 里
        # (config_model.extract_groups 顶层必须是 $ref 嵌套模型, 否则 KeyError);
        # ②旧后端进程的 config 模型没有 lantern_config 字段, 直接访问会
        # AttributeError(8-05 20:29 主号陪1 实测), 双层 getattr 兜底旧 config
        # 取默认 False=不跳过, 不炸。
        if getattr(getattr(self.conf, 'lantern_config', None), 'skip_lantern', False):
            logger.info('skip_lantern enabled, skip 4 lanterns and go boss directly')
            return
        # 然后到四个灯笼
        match_click = {
            1: self.C_DE_1,
            2: self.C_DE_2,
            3: self.C_DE_3,
            4: self.C_DE_4,
        }
        for i in range(1, 5):
            logger.hr(f'Check lantern {i}', 3)
            lantern_type = self.check_lantern(i)
            self.device.click_record_clear()
            match lantern_type:
                case LanternClass.BOX:
                    self._box(match_click[i])
                case LanternClass.MAIL:
                    self._mail(match_click[i])
                case LanternClass.REALM:
                    self._realm(match_click[i])
                case LanternClass.EMPTY:
                    logger.warning(f'Lantern {i} is empty')
                case LanternClass.BATTLE:
                    self._battle(match_click[i])
                case LanternClass.MYSTERY:
                    self._mystery(match_click[i])
                case LanternClass.BOSS:
                    self._boss(match_click[i])
            time.sleep(1)

    def check_lantern(self, index: int = 1):
        """
        检查灯笼的类型
        :param index: 四个灯笼，从1开始
        :return:
        """
        match_roi = {
            1: self.C_DE_1.roi_front,
            2: self.C_DE_2.roi_front,
            3: self.C_DE_3.roi_front,
            4: self.C_DE_4.roi_front,
        }
        match_empty = {
            1: self.I_DE_DEFEAT_1,
            2: self.I_DE_DEFEAT_2,
            3: self.I_DE_DEFEAT_3,
            4: self.I_DE_DEFEAT_4,
        }
        self.I_DE_BOX.roi_back = match_roi[index]
        self.I_DE_LETTER.roi_back = match_roi[index]
        self.I_DE_MYSTERY.roi_back = match_roi[index]
        self.I_DE_REALM.roi_back = match_roi[index]
        self.I_DE_FIND_BOSS.roi_back = match_roi[index]
        target_box = self.I_DE_BOX
        target_letter = self.I_DE_LETTER
        target_mystery = self.I_DE_MYSTERY
        target_realm = self.I_DE_REALM
        target_find_boss = self.I_DE_FIND_BOSS
        target_empty = match_empty[index]

        # 开始判断
        self.screenshot()
        if self.appear(target_box):
            logger.info(f'Lantern {index} is box')
            return LanternClass.BOX
        elif self.appear(target_letter):
            logger.info(f'Lantern {index} is letter')
            return LanternClass.MAIL
        elif self.appear(target_mystery):
            logger.info(f'Lantern {index} is mystery task')
            return LanternClass.MYSTERY
        elif self.appear(target_realm):
            logger.info(f'Lantern {index} is realm')
            return LanternClass.REALM
        elif self.appear(target_empty):
            logger.info(f'Lantern {index} is empty')
            return LanternClass.EMPTY
        elif self.appear(target_find_boss):
            logger.info(f'Lantern {index} is boss')
            return LanternClass.BOSS
        else:
            # 无法判断是否是战斗的还是结界的
            logger.info(f'Lantern {index} is battle')
            return LanternClass.BATTLE

    def _box(self, target_click):
        box_buy_config = self.config.demon_encounter.box_buy_config
        while 1:
            self.screenshot()
            if self.appear(self.I_JADE_50):
                break
            if self.click(target_click, interval=1):
                continue
        while 1:
            self.screenshot()
            if not self.appear(self.I_MYSTERY_AMULET) and not (box_buy_config.box_buy_sushi and self.appear(self.I_SUSHI)):
                if self.appear_then_click(self.I_DE_FIND, interval=2.5):
                    break
            # 默认购买蓝票
            if self.appear(self.I_MYSTERY_AMULET):
                logger.info('Buy a mystery amulet for 50 jade')
                self.click(self.I_JADE_50)
                continue
            # 可选购买体力
            if box_buy_config.box_buy_sushi and self.appear(self.I_SUSHI):
                logger.info('Buy one hundred sushi for 50 jade')
                self.click(self.I_JADE_50)
                continue

    def _mail(self, target_click):
        # 答题
        def answer():
            click_match = {
                1: self.C_ANSWER_1,
                2: self.C_ANSWER_2,
                3: self.C_ANSWER_3,
            }
            index = None
            self.screenshot()
            question = self.O_LETTER_QUESTION.detect_text(self.device.image)
            question = question.replace('?', '').replace('？', '')
            answer_1 = self.O_LETTER_ANSWER_1.detect_text(self.device.image)
            answer_2 = self.O_LETTER_ANSWER_2.detect_text(self.device.image)
            answer_3 = self.O_LETTER_ANSWER_3.detect_text(self.device.image)
            if answer_1 == '其余选项皆对':
                index = 1
            elif answer_2 == '其余选项皆对':
                index = 2
            elif answer_3 == '其余选项皆对':
                index = 3
            if not index:
                index = Answer().answer_one(question=question, options=[answer_1, answer_2, answer_3])
            if index is None:
                index = 1
            logger.info(f'Question: {question}, Answer: {index}')
            return click_match[index]

        while 1:
            self.screenshot()
            if self.appear(self.I_LETTER_CLOSE):
                break
            if self.click(target_click, interval=1):
                continue
        logger.info('Question answering Start')
        for i in range(1, 4):
            # 还未测试题库无法识别的情况
            logger.hr(f'Answer {i}', 3)
            answer_click = answer()
            while 1:
                self.screenshot()
                if self.ui_reward_appear_click():
                    time.sleep(0.5)
                    while 1:
                        self.screenshot()
                        # 等待动画结束
                        if not self.appear(self.I_UI_REWARD, threshold=0.6):
                            logger.info('Get reward success')
                            break
                        # 一直点击
                        if self.ui_reward_appear_click():
                            continue
                    break
                # 如果没有出现红色关闭按钮，说明答题结束
                if not self.appear(self.I_LETTER_CLOSE):
                    time.sleep(1.8)
                    self.screenshot()
                    if not self.appear(self.I_LETTER_CLOSE):
                        logger.warning('Answer finish')
                        return

                # 一直点击
                self.click(answer_click, interval=1.5)
            time.sleep(0.5)

    def _battle(self, target_click):
        # 点击上限保护: 连续点击 N 次仍未进入战斗则放弃该灯笼, 防止死循环触发
        # 60s 卡死/TooManyClick 重启。8-02 实测: LETTER 灯笼匹配 0.767<0.8 阈值被
        # check_lantern 排除法误判为 BATTLE, _battle 连点 10 次 de_3 无反应 ->
        # GameTooManyClickError 重启 (小号2/3/5/6)。正常战斗灯笼 1-2 次点击即进入
        # 战斗(I_DE_LOCATION 消失), 6 次上限正常流程永远碰不到。
        click_count = 0
        click_limit = 6
        while 1:
            self.screenshot()
            if not self.appear(self.I_DE_LOCATION):
                logger.info('Battle Start')
                break
            if self.appear(self.I_DE_SMALL_FIRE):
                # 小鬼王
                logger.info('Small Boss')
                while 1:
                    self.screenshot()
                    if not self.appear(self.I_DE_SMALL_FIRE):
                        break
                    if self.appear_then_click(self.I_DE_SMALL_FIRE, interval=1):
                        continue
                break

            if click_count >= click_limit:
                logger.warning(f'Click lantern {click_limit} times no battle start, skip this lantern')
                return
            if self.click(target_click, interval=1):
                click_count += 1
                continue
        self.current_count = 0
        if self.run_general_battle():
            logger.info('Battle End')

    def _realm(self, target_click):
        # 结界
        # 与 _battle 相同的点击上限保护(防御): 灯笼类型误判时防死循环触发卡死重启
        click_count = 0
        click_limit = 6
        while 1:
            self.screenshot()
            if not self.appear(self.I_DE_LOCATION):
                logger.info('Battle Start')
                break
            if self.appear_then_click(self.I_DE_REALM_FIRE, interval=0.7):
                continue

            if click_count >= click_limit:
                logger.warning(f'Click lantern {click_limit} times no realm start, skip this lantern')
                return
            if self.click(target_click, interval=1):
                click_count += 1
                continue
        self.current_count = 0
        if self.run_general_battle():
            logger.info('Battle End')

    def _mystery(self, target_click):
        # 神秘任务， 不做
        pass

    def _boss(self, target_click):
        # 运气爆表，点灯笼出现大鬼王
        while 1:
            self.screenshot()
            if self.appear(self.I_BOSS_KILLED):
                # 这个大鬼王已经击败
                logger.warning('Boss already killed')
                self.ui_click_until_disappear(self.I_UI_BACK_RED)
                break
            if self.appear(self.I_BOSS_FIRE):
                self.execute_boss()
                break
            if self.click(target_click, interval=2.3):
                continue

    def check_time(self):
        """
        检查时间是否正确，
        如果正确就继续
        如果不在17:00到22:00之间,就推迟到下一个 17:30
        :return:
        """
        now = datetime.now()
        if now.hour < 17:
            # 17点之前，推迟到当天的17点半
            logger.info('Before 17:00, wait to 17:30')
            target_time = datetime(now.year, now.month, now.day, 17, 30, 0)
            self.set_next_run(task='DemonEncounter', success=False, finish=False, target=target_time)
            return False
        elif now.hour >= 23:
            # 23点之后，推迟到第二天的17:30
            logger.info('After 23:00, wait to 17:30')
            target_time = datetime(now.year, now.month, now.day, 17, 30, 0) + timedelta(days=1)
            self.set_next_run(task='DemonEncounter', success=False, finish=False, target=target_time)
            return False
        else:
            return True

    @property
    def boss_type(self) -> str:
        boss_name = BossType(datetime.now().weekday()).name
        if self.best_demon_enable:
            return f'best_demon_{boss_name}'
        return f'demon_{boss_name}'

    @property
    def best_demon_enable(self) -> bool:
        boss_name = BossType(datetime.now().weekday()).name
        return getattr(self.conf.best_demon_boss_config, f'best_demon_{boss_name}_select', False)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('du')
    d = Device(c)
    t = ScriptTask(c, d)

    t.run()
