# This Python file uses the following encoding: utf-8
# AccountDaily 账号日常任务 —— 执行逻辑
# 2026-08-07 v2 需求变更版:
#   1. 按账号顺序依次执行, 每个账号执行自己配置的任务清单(不管任务的定时 next_run)
#   2. 单任务失败重试 3 次仍失败 → 跳过该任务, 继续该号下一任务(不中断整个流程)
#   3. 切号失败重试 3 次仍失败 → 跳过该账号
#   4. 异常分类处理(防连锁失败): TaskEnd=成功 / RequestHumanTakeover=透传请人工 /
#      GameStuck+GameNotRunning=先重启游戏再重试(否则重试必败) / 普通异常=直接重试
#   5. 结束不切回账号1、不记录进度、每次从头完整跑(SwitchAccount 每次"退出→登录目标", 不依赖起点)
#   6. 日志信息要全(用户强调): 每号每任务 hr 分隔 + 汇总统计 + 失败原因, 全部带 AccountDaily 前缀

from pathlib import Path

from module.exception import TaskEnd, RequestHumanTakeover, GameStuckError, GameNotRunningError, AccountLoginFailed
from module.logger import logger
from tasks.AccountDaily.config import AccountDaily
from tasks.Component.Login.service import LoginService
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main

# 失败重试次数上限(用户要求: 重试3次, 还不行就跳过)
RETRY_TIMES = 3


class ScriptTask(GameUi):
    conf: AccountDaily = None

    def run(self):
        self.conf = self.config.account_daily
        account_list = self.conf.account_list or []
        logger.hr('AccountDaily 开始', level=0)
        logger.info('AccountDaily: 共 %d 个账号', len(account_list))
        if not account_list:
            logger.warning('AccountDaily: 账号列表为空, 直接结束')
            self.set_next_run('AccountDaily', success=True)
            raise TaskEnd('AccountDaily')

        # 0. 无人值守: 确保模拟器和游戏已启动(挂机场景模拟器常开时几乎零开销)
        # 用户要求(8-08): 模拟器和游戏都要自动启动, 一键跑无需人工先开模拟器
        if not self._ensure_emulator_and_game():
            logger.error('AccountDaily: 模拟器/游戏启动失败, 中止本轮(请检查模拟器配置)')
            self.set_next_run('AccountDaily', success=False)
            raise TaskEnd('AccountDaily')

        # 汇总统计(结束打印汇总表, 一眼看出每个号哪些任务没跑)
        summary = []

        for idx, account in enumerate(account_list, 1):
            logger.hr(f'AccountDaily 账号 {idx}/{len(account_list)} [{account.character}]', level=1)
            if not account.is_valid():
                logger.warning('AccountDaily: 账号无效(角色名为空), 跳过')
                continue

            tasks = account.get_task_list()
            if not tasks:
                logger.warning('AccountDaily: 账号 %s 未配置任务, 跳过', account.character)
                continue
            # 任务清单打印出来, 方便一眼核对配置是否符合预期
            logger.info('AccountDaily: 任务清单(%d个): %s', len(tasks), tasks)

            # 切号(失败重试 RETRY_TIMES 次, 仍失败跳过该账号; 进入游戏失败直接跳过不重试)
            # 失败原因与跳过日志已在 _switch_account_with_retry 内部详细输出(含截图路径)
            if not self._switch_account_with_retry(account):
                summary.append((account.character, '切号失败', '', ''))
                continue

            # 逐个执行该号任务
            acc_success = acc_failed = 0
            for j, task_name in enumerate(tasks, 1):
                ok = self._run_task_with_retry(task_name, j, len(tasks))
                if ok:
                    acc_success += 1
                else:
                    acc_failed += 1

            # 记录完成时间(前端卡片显示"上次跑过"), 保存配置
            self.conf.update_account_login_history(account)
            self.config.save()
            logger.info('AccountDaily: 账号 %s 完成: 成功%d 失败%d', account.character, acc_success, acc_failed)
            summary.append((account.character, f'{acc_success}', f'{acc_failed}', '完成'))

        # 汇总表
        logger.hr('AccountDaily 汇总', level=1)
        for item in summary:
            logger.info('AccountDaily: 账号 %s 成功%s 失败%s %s', *item)
        logger.hr('AccountDaily 完成', level=0)
        self.set_next_run('AccountDaily', success=True)
        raise TaskEnd('AccountDaily')

    # ------------------------------------------------------------------ 切号

    def _to_account_info(self, account):
        """
        AccountDailyItem → SwitchAccount 需要的 AccountInfo。
        为什么: SwitchAccount.login() 内部调用 AccountInfo.is_account_alias(OCR 账号比对)
        和 preprocessAccount, AccountDailyItem 没有这些方法(8-08 实测 AttributeError);
        两模型字段同构(character/svr/account/account_alias/apple_or_android/switch_svr/last_complete_time),
        转换后即可复用整个切号链路, 且不破坏 AccountDaily 自己的 config 结构。
        """
        return AccountInfo(
            character=account.character,
            svr=account.svr,
            account=account.account,
            account_alias=account.account_alias,
            apple_or_android=account.apple_or_android,
            switch_svr=account.switch_svr,
            last_complete_time=account.last_complete_time,
        )

    def _switch_account_with_retry(self, account) -> bool:
        """
        切号并处理登录弹窗, 失败重试 RETRY_TIMES 次。
        为什么重试: 切号失败多是 OCR 识别问题(角色名/账号识别不准), 重试往往能成功,
        直接跳过会导致该号任务全部漏跑。重试仍失败才跳过账号(不能中断整个流程)。
        2026-08-13 补充: 登录进游戏失败(AccountLoginFailed)不进入重试 ——
        LoginService 内部已"重启游戏重试 2 次", 再重试切号会变成 3×2=6 次登录尝试,
        且多为回归号/游戏加载问题, 重试意义不大, 直接跳过该账号(用户 8-13 指示)。
        """
        for attempt in range(1, RETRY_TIMES + 1):
            logger.info('AccountDaily: 切号 %s-%s 第 %d/%d 次', account.character, account.svr, attempt, RETRY_TIMES)
            try:
                ok = SwitchAccount(self.config, self.device, self._to_account_info(account)).switchAccount()
            except AccountLoginFailed as e:
                # 进入游戏失败(点"进入游戏"后重启重试 2 次仍未识别到庭院) → 跳过该账号, 不重试
                # 8-10 实测: 点错服务器同名角色(回归号)后 60s 未进庭院, 原逻辑人工接管整批全灭
                self._save_failure_screenshot(account.character)
                logger.error('AccountDaily: 账号 %s 进入游戏失败(重启游戏重试2次后仍未识别到庭院), 跳过该账号: %s',
                             account.character, e)
                return False
            except RequestHumanTakeover:
                # 登录界面出现无法处理的异常(如未知页面), 需要人工介入 → 透传, 由调度器 exit(1)
                logger.critical('AccountDaily: 切号 %s 请求人工接管, 透传给调度器', account.character)
                raise
            except Exception as e:
                logger.error('AccountDaily: 切号 %s 异常: %s', account.character, e)
                ok = False
            if ok:
                logger.info('AccountDaily: 切号成功 %s', account.character)
                return True
            logger.warning('AccountDaily: 切号失败 %s 第 %d/%d 次', account.character, attempt, RETRY_TIMES)
        logger.error('AccountDaily: 账号 %s 切号失败(%d次), 跳过该账号', account.character, RETRY_TIMES)
        return False

    def _save_failure_screenshot(self, character: str):
        """
        登录进游戏失败时保存现场截图(log/screenshots/), 便于事后人工核对:
        卡在什么画面、是不是点错服务器/回归号。文件名是毫秒时间戳, 日志输出完整路径。
        """
        try:
            self.device.save_screenshot(genre='AccountDaily')
            folder = Path('./log/screenshots')
            files = sorted(folder.glob('*.png'), key=lambda f: f.stat().st_mtime, reverse=True)
            if files:
                logger.info('AccountDaily: 账号 %s 失败截图已保存: %s', character, files[0])
            else:
                logger.warning('AccountDaily: 账号 %s 失败截图保存失败(目录为空)', character)
        except Exception as e:
            logger.warning('AccountDaily: 账号 %s 保存失败截图异常: %s', character, e)

    # ------------------------------------------------------------------ 单任务

    def _run_task_with_retry(self, task_name: str, j: int, total: int) -> bool:
        """
        动态加载并执行单个任务, 失败重试 RETRY_TIMES 次, 仍失败返回 False(跳过该任务)。
        注意: 这是"该账号第j个任务", 失败只影响当前任务, 不影响该号后续任务和后续账号。
        """
        # 预检任务文件存在性: 防手滑配错任务名(枚举理论上不会错, 但历史 config 可能残留), 避免白耗3次重试
        module_path = Path.cwd() / 'tasks' / task_name / 'script_task.py'
        if not module_path.exists():
            logger.warning('AccountDaily: tasks/%s/script_task.py 不存在, 跳过(请检查任务名)', task_name)
            return False

        for attempt in range(1, RETRY_TIMES + 1):
            logger.hr(f'AccountDaily 任务 {j}/{total}: {task_name} (第{attempt}次)', level=2)
            try:
                self._run_once(task_name, module_path)
                logger.info('AccountDaily: 任务 %s 成功', task_name)
                return True
            except TaskEnd:
                # 任务正常结束(所有任务都以 TaskEnd 收尾, 视为成功, 不消耗重试次数)
                logger.info('AccountDaily: 任务 %s 正常结束(TaskEnd)', task_name)
                return True
            except RequestHumanTakeover:
                # 任务内出现无法自动处理的局面, 必须人工 → 透传, 由调度器 exit(1); 绝不吞掉
                logger.critical('AccountDaily: 任务 %s 请求人工接管, 透传给调度器', task_name)
                raise
            except (GameStuckError, GameNotRunningError) as e:
                # 游戏卡死/未运行: 重试无意义(游戏都没了, 重试只会快速再失败),
                # 必须先恢复游戏(自动拉起)再计入一次失败 —— 8-08 用户要求支持无人值守,
                # 不再依赖调度器跑 Restart(该任务可能被 disable)
                logger.error('AccountDaily: 任务 %s 游戏异常(%s), 恢复游戏后重试', task_name, type(e).__name__)
                self._recover_game()
                self.device.sleep(10)
            except Exception as e:
                logger.error('AccountDaily: 任务 %s 异常: %s', task_name, e)
                logger.exception(e)

            # 失败路径: 重试前回到庭院归一页面(从失败现场直接重跑大概率再失败);
            # 最后一次失败不需要归一(该任务已被跳过, 下一个任务会自行导航)
            if attempt < RETRY_TIMES:
                self._goto_main_safe()

        logger.error('AccountDaily: 任务 %s 失败 %d 次, 跳过(继续下一任务)', task_name, RETRY_TIMES)
        return False

    def _run_once(self, task_name: str, module_path: Path):
        """
        仿 FindJade CreatObjectFromModule: 动态加载 tasks/<Task>/script_task.py 并直接 run(), 不走调度器。
        子任务 run() 内部会自己 set_next_run 推进自己的 next_run(副作用, 无害:
        专用 config 里其他任务都 disable, 调度器不会单独选它们; 下次 AccountDaily 直接 run 不看 next_run)。
        """
        from module.base.utils import load_module
        module = load_module('script_task', str(module_path))
        module.ScriptTask(config=self.config, device=self.device).run()

    def _goto_main_safe(self):
        """
        失败后回庭院归一页面。失败不影响主流程, 只记日志。
        注意: goto_page 有已知导航横跳卡死风险(未解决), 故只在失败重试路径调用, 正常路径不额外导航
        (子任务自身负责从庭院出发的导航, 切号成功时本来就在庭院)。
        """
        try:
            self.goto_page(page_main)
            logger.info('AccountDaily: 已回到庭院(页面归一)')
        except Exception as e:
            logger.warning('AccountDaily: 页面归一失败(不影响后续重试): %s', e)

    # ------------------------------------------------------------------ 模拟器/游戏自动启动(无人值守)

    def _ensure_emulator_and_game(self) -> bool:
        """
        确保模拟器和游戏已启动(用户 8-08 要求: 一键启动, 无需人工先开模拟器)。
        调用时机: run() 开头执行一次。中途游戏挂了由 _recover_game() 处理。
        """
        if not self._ensure_emulator():
            return False
        if not self._ensure_game():
            return False
        return True

    def _ensure_emulator(self) -> bool:
        """
        模拟器未启动则拉起。device.emulator_start() 内部自带:
        目标实例检测 → 已在线直接返回 / 未在线启动(重试3次) → emulator_start_watch 等就绪。
        模拟器已开时该调用几乎零开销(内部先判在线直接返回)。
        """
        try:
            ok = self.device.emulator_start()
            if not ok:
                logger.error('AccountDaily: 模拟器启动失败(已重试3次)')
                return False
            logger.info('AccountDaily: 模拟器就绪')
            return True
        except Exception as e:
            logger.error('AccountDaily: 模拟器启动异常: %s', e)
            return False

    def _ensure_game(self) -> bool:
        """
        游戏未在前台运行则启动(进程被杀会重新拉起, 在后台会切到前台); 已在前台直接返回。
        启动后处理登录弹窗(LoginService.app_handle_login), 与 Restart 任务同款逻辑。
        """
        try:
            if self.device.app_is_running():
                logger.info('AccountDaily: 游戏已在运行')
                return True
            logger.hr('AccountDaily: 启动游戏', level=1)
            self.device.app_start()
            self.device.wait_app_start_ready()
            LoginService(config=self.config, device=self.device).app_handle_login()
            logger.info('AccountDaily: 游戏启动完成')
            return True
        except Exception as e:
            logger.error('AccountDaily: 游戏启动异常: %s', e)
            return False

    def _recover_game(self):
        """
        游戏卡死/未运行时的恢复(任务重试前调用):
        - 进程已死 → 直接拉起
        - 在后台 → 切到前台
        - 在前台但卡死 → 重启游戏进程
        另保留 task_call('Restart') 双保险: restart enable 时调度器会按 Restart 任务完整恢复
        (含模拟器), disable 时该调用被忽略, 不影响本方法已完成的直接恢复。
        """
        try:
            if not self.device.app_is_alive():
                logger.info('AccountDaily: 游戏进程已死, 重新拉起')
                self._ensure_game()
            elif not self.device.app_is_running():
                logger.info('AccountDaily: 游戏在后台, 切到前台')
                self._ensure_game()
            else:
                logger.info('AccountDaily: 游戏卡死, 重启游戏进程')
                self.device.app_stop()
                self._ensure_game()
        except Exception as e:
            logger.warning('AccountDaily: 游戏恢复失败: %s', e)
        try:
            self.config.task_call('Restart')
        except Exception as e:
            logger.warning('AccountDaily: task_call(Restart) 失败: %s', e)
