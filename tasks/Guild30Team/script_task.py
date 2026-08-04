# This Python file uses the following encoding: utf-8
# @author runhey
# 大小号联动刷寮三十 (Guild30Team)
# 核心思路: 号1(队长) 与 号2(队员) 通过 HTTP(主进程 :22288) + 共享状态文件握手,
#           然后各自进入御魂/觉醒组队界面, 队长开房邀请, 队员接受邀请入队,
#           组队刷 battle_count 次 (必须组队才算, 无单刷), 刷完各自回寮三十领奖。
# 健壮性: 任何环节失败一律跳过任务(success=False, TaskEnd), 绝不单刷、不卡死、不重启;
#         所有长等待挂 PAUSE/BATTLE_STATUS_S 防 60s 空集卡死。
import json
import time
import uuid
import requests
from datetime import datetime, timedelta
from pathlib import Path

from module.exception import TaskEnd, GameStuckError
from module.logger import logger
from module.base.timer import Timer

from tasks.GameUi.game_ui import GameUi
# page_collective_missions 定义在 CollectiveMissions 任务自己的 page.py (tasks/CollectiveMissions/page.py),
# 不在 tasks/GameUi/page.py —— 从 GameUi.page 导入会 ImportError (8-04 实测启动即炸, 修正导入源)
from tasks.GameUi.page import page_main
from tasks.CollectiveMissions.page import page_collective_missions
from tasks.Component.GeneralRoom.general_room import GeneralRoom
from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Guild30Team.assets import Guild30TeamAssets
from tasks.Guild30Team.config import DungeonType, Guild30UserStatus

# 共享状态文件: 放 config/guild30/ 子目录, 不会被 all_script_files() 扫成账号配置
# (glob('*.json') 只匹配 config/ 顶层, 不匹配子目录)
# 全部是**覆盖写**(write_text 单行 JSON), 固定路径固定内容, 文件不会无限增长;
# 旧残留靠 request_id(uuid, 每次任务生成)匹配 + ts 新鲜度过滤, 无需手动清理。
STATE_DIR = Path.cwd() / 'config' / 'guild30'
REQUEST_FILE = STATE_DIR / 'request.json'
ACK_FILE = STATE_DIR / 'ack.json'
# 队员"已在房间"回报: 队员 is_in_room 时写 {ts, request_id}, 队长开战前检查,
# 确认搭档真在房间(而非路人占位)才点挑战 —— 8-04 新增的双确认第2环
JOINED_FILE = STATE_DIR / 'joined.json'
# 主进程 API (server.py 的 FastAPI), 所有账号子进程共用
SERVER = 'http://127.0.0.1:22288'
# 陈旧 request 判定阈值: 超过该时长视为上次残留, 忽略
REQUEST_STALE_SECONDS = 600
# 入队回报新鲜窗口: 队长开战前确认 joined.ts 距现在不超过该秒数才放行
# (队员进房即写, 战斗间隙回房也刷新; 120s 远大于 run_invite 的等待超时, 且
#  能挡住"队员中途掉线/被踢出"后的陈旧回报)
JOINED_FRESH_SECONDS = 120

class ScriptTask(GeneralBattle, GeneralInvite, GeneralRoom, GameUi, Guild30TeamAssets):
    """大小号联动刷寮三十"""

    _request: dict = None  # 握手阶段读到的请求文件(队员侧用于取战斗次数)

    def run(self) -> None:
        # 字段名注意: config_model.py 里该任务字段是 guild_3_0_team
        # (convert_to_underscore('Guild30Team') 会把数字拆开, 字段名必须与其一致)
        conf = self.config.guild_3_0_team.guild30_config

        # 8-04: 任务开头打配置摘要, 便于从日志直接确认"用户实际配了什么"
        # (曾出现 user_status=队员 导致走错分支、partner 指向未配置账号等难排查问题)
        logger.info(f'[Guild30] Start task: user_status={conf.user_status.value}, '
                    f'partner={conf.partner_config}, dungeon={conf.dungeon_type.value}, '
                    f'count={conf.battle_count}, claim_reward={conf.claim_reward}, '
                    f'start_partner={conf.start_partner}, ack_timeout={conf.ack_timeout}s')

        # 阶段1: 握手(通信层, 独立于游戏内流程)
        try:
            ok = self._handshake(conf)
        except (GameStuckError, TaskEnd):
            raise  # 卡死保护/任务结束必须冒泡到调度器, 不能吞
        except Exception as e:
            logger.error(f'Handshake error: {e}')
            ok = False
        if not ok:
            self._finish(success=False, reason='handshake failed')

        # 阶段2: 组队刷本(游戏内流程, 复用 Orochi/EvoZone 的队长/队员逻辑)
        logger.info('[Guild30] Handshake passed, start farming')
        success = self._run_farm(conf)

        # 阶段3: 领奖(各自执行, 复用 CollectiveMissions 领奖逻辑)
        if success and conf.claim_reward:
            self._claim_reward()

        self._finish(success=success, reason='farm done')

    # ============================== 阶段1: 握手 ==============================

    def _handshake(self, conf) -> bool:
        if conf.user_status == Guild30UserStatus.LEADER:
            return self._handshake_leader(conf)
        return self._handshake_member(conf)

    def _handshake_leader(self, conf) -> bool:
        """队长侧: 写请求文件 -> 拉起/通知号2 -> 轮询 ack"""
        request = {
            'partner': conf.partner_config,
            'dungeon': conf.dungeon_type.value,
            'count': conf.battle_count,
            'ts': time.time(),
            'role': 'leader',
            # 唯一请求 id: ack 回写同一 id, 队长按 id 匹配判断是本次的 ack
            # (不用时间戳比较: 号2 响应极快时 ts 可能相等, 字符串/浮点比较都有边界)
            'request_id': str(uuid.uuid4()),
        }
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            REQUEST_FILE.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
            # 打印 request_id 前8位, 与后续 ack/joined 日志对得上
            logger.info(f'[Guild30] Leader request written: partner={conf.partner_config}, '
                        f'dungeon={conf.dungeon_type.value}, count={conf.battle_count}, '
                        f'request_id={request["request_id"][:8]}')
        except Exception as e:
            logger.error(f'[Guild30] Write request file failed: {e}')
            return False

        # 1.5 确保队员的 Guild30Team 已 enable 且身份为队员 (8-04: 主号一键全自动,
        # 小号没配置也自动补, 否则拉起来也会被调度器 enable=False 跳过)
        self._ensure_partner_configured(conf.partner_config, want_leader=False, conf=conf)

        # 2. 检查号2是否启动; 未启动则按 start_partner 决定拉起还是跳过
        running = self._query_partner_running(conf.partner_config)
        logger.info(f'[Guild30] Partner {conf.partner_config} process running: {running}')
        if not running:
            if conf.start_partner:
                if not self._start_partner(conf.partner_config):
                    logger.error(f'[Guild30] Partner {conf.partner_config} start failed, skip task')
                    return False
                logger.info(f'[Guild30] Partner {conf.partner_config} started, wait it online')
            else:
                logger.error(f'[Guild30] Partner {conf.partner_config} not running and start_partner disabled, skip task')
                return False

        # 3. 通知号2 立即执行 Guild30Team (改号2 config next_run, 号2 idle 时 mtime 感知重载)
        if not self._notify_partner(conf.partner_config):
            logger.error(f'[Guild30] Notify partner {conf.partner_config} failed, skip task')
            return False

        # 4. 轮询 ack (超时 ack_timeout), 等待期间挂 PAUSE 防 60s 空集卡死
        self.device.stuck_record_clear()
        self.device.stuck_record_add('PAUSE')
        timer = Timer(conf.ack_timeout)
        timer.start()
        try:
            while not timer.reached():
                time.sleep(3)
                ack = self._read_ack()
                # 按 request_id 匹配: 只有本次 request 对应的 ack 才算数
                if ack and ack.get('request_id') == request['request_id']:
                    logger.info(f'[Guild30] Partner ack received (request_id={request["request_id"][:8]}), '
                                f'handshake passed, start farming')
                    # 记录本次 request(含 request_id), 后续"搭档在房间"确认(joined)要用
                    self._request = request
                    return True
            logger.error(f'[Guild30] Partner ack timeout after {conf.ack_timeout}s, skip task')
            return False
        finally:
            self.device.stuck_record_clear()

    def _handshake_member(self, conf) -> bool:
        """队员侧: 读请求文件(校验 partner 是自己 + 时间戳新鲜) -> 写 ack。
        8-04 修复: 没有 request 文件时**不再当场失败**, 而是主动把队长(搭档)拉起来:
        确保其 Guild30Team 配置就绪 -> 拉起进程 -> 通知立即执行 -> 轮询等待其写 request。
        用户要求"只要主号配置了即可, 小号没配置/没启动也自动拉起", 主号配成队员也能一键跑。"""
        request = self._read_request()
        if request:
            ok, reason = self._verify_request(request, conf)
            if not ok:
                # request 存在但不属于自己/已陈旧: 当作没有, 走拉起-等待流程
                # (拉起队长后队长会重新写覆盖旧 request)
                logger.info(f'[Guild30] Request exists but invalid ({reason}), '
                            f'will start partner and wait new request')
            else:
                logger.info(f'[Guild30] Member got valid request: partner={request.get("partner")}, '
                            f'count={request.get("count")}, request_id={str(request.get("request_id",""))[:8]}')
                return self._write_ack(request)
        logger.info('[Guild30] No valid guild30 request, start partner (leader) and wait for its request')
        return self._wait_leader_request(conf)

    def _wait_leader_request(self, conf) -> bool:
        """队员启动时没有有效 request: 确保队长配置就绪 -> 拉起队长进程 -> 通知立即执行
        -> 轮询等待 request (挂 PAUSE 防 60s 空集卡死, 上限 ack_timeout)。
        队长(搭档)写 request 后本方法校验并回 ack, 双方即可进入组队刷本。"""
        partner = conf.partner_config
        # 1. 确保队长 Guild30Team 已 enable 且身份为队长 (没配则自动补)
        self._ensure_partner_configured(partner, want_leader=True, conf=conf)
        # 2. 拉起队长进程(仅未运行时才 start, start 会重启已在跑的进程)
        running = self._query_partner_running(partner)
        logger.info(f'[Guild30] Partner {partner} process running: {running}')
        if not running:
            if conf.start_partner:
                if not self._start_partner(partner):
                    logger.error(f'[Guild30] Partner {partner} start failed, skip task')
                    return False
                logger.info(f'[Guild30] Partner {partner} started, wait it online')
            else:
                logger.error(f'[Guild30] Partner {partner} not running and start_partner disabled, skip task')
                return False
        # 3. 通知队长立即执行 Guild30Team (改 next_run, 队长调度器 mtime 感知后重载执行)
        if not self._notify_partner(partner):
            logger.error(f'[Guild30] Notify partner {partner} failed, skip task')
            return False
        # 4. 轮询等待 request (长等待挂 PAUSE, 超过 60s 也不会触发空集卡死)
        logger.info(f'[Guild30] Wait leader request, timeout={conf.ack_timeout}s')
        self.device.stuck_record_clear()
        self.device.stuck_record_add('PAUSE')
        timer = Timer(conf.ack_timeout)
        timer.start()
        try:
            while not timer.reached():
                time.sleep(3)
                request = self._read_request()
                if request:
                    ok, reason = self._verify_request(request, conf)
                    if not ok:
                        logger.warning(f'[Guild30] Request invalid ({reason}), keep waiting')
                        continue
                    logger.info(f'[Guild30] Leader request received: partner={request.get("partner")}, '
                                f'count={request.get("count")}, request_id={str(request.get("request_id",""))[:8]}')
                    return self._write_ack(request)
            logger.error(f'[Guild30] Wait leader request timeout after {conf.ack_timeout}s, skip task')
            return False
        finally:
            self.device.stuck_record_clear()

    @staticmethod
    def _read_request() -> dict:
        """读 request 文件, 不存在/解析失败返回空 dict"""
        try:
            return json.loads(REQUEST_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}

    def _verify_request(self, request: dict, conf) -> tuple:
        """校验 request 是否针对自己且新鲜: (ok, reason)
        返回 ok=False 视为无效 request (不存在/陈旧/partner 不匹配都会走拉起-等待流程)"""
        if request.get('partner') != self.config.config_name:
            return False, f'partner {request.get("partner")} != self {self.config.config_name}'
        req_ts = request.get('ts', 0)
        if not isinstance(req_ts, (int, float)) or req_ts <= 0:
            return False, 'ts invalid'
        if time.time() - req_ts > REQUEST_STALE_SECONDS:
            return False, f'stale (>{REQUEST_STALE_SECONDS}s)'
        return True, ''

    def _write_ack(self, request: dict) -> bool:
        """写 ack 回执(回写同 request_id, 队长按 id 匹配确认是本次请求的就绪响应)。
        同时记录 request 供 _run_farm 取战斗次数(以队长配置为准)。"""
        self._request = request
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            ACK_FILE.write_text(json.dumps({'ts': time.time(), 'request_id': request.get('request_id', '')}),
                                encoding='utf-8')
            logger.info(f'[Guild30] Ack written, ready to farm: dungeon={request.get("dungeon")}, count={request.get("count")}')
            return True
        except Exception as e:
            logger.error(f'[Guild30] Write ack file failed: {e}')
            return False

    def _ensure_partner_configured(self, partner: str, want_leader: bool, conf) -> None:
        """确保搭档的 Guild30Team 已 enable 且身份/搭档/副本/次数与本次一致。
        通过主进程 HTTP 端点修改 (PUT /{partner}/Guild30Team/{group}/{argument}/value),
        不直接改文件 —— 后端 config_cache 有内存态, 直改文件会被 auto save 覆盖。
        8-04 新增: 用户要求"只要主号配置了即可, 小号没配置/没启动也自动拉起",
        所以拉起前先保证搭档配置就绪, 否则拉起来也会被调度器 enable=False 跳过。

        Args:
            partner: 搭档 config 名
            want_leader: True=把搭档配置为队长(本账号是队员在等它开房);
                         False=把搭档配置为队员(本账号是队长在等它入队)
            conf: 本账号 Guild30Config (副本/次数按主号配置同步给搭档)
        """
        # 目标身份: 搭档角色固定为对侧
        target_status = Guild30UserStatus.LEADER.value if want_leader else Guild30UserStatus.MEMBER.value
        try:
            resp = requests.get(f'{SERVER}/{partner}/Guild30Team/args', timeout=5)
            if resp.status_code != 200:
                logger.warning(f'[Guild30] Query partner {partner} guild30 config failed ({resp.status_code}), skip auto-config')
                return
            data = resp.json()
        except Exception as e:
            logger.warning(f'[Guild30] Query partner {partner} guild30 config failed: {e}')
            return

        def find_val(group: str, name: str):
            for item in data.get(group, []):
                if item.get('name') == name:
                    return item.get('value')
            return None

        # 已就绪则不动: enable=True + 身份正确 + 搭档指向自己
        cur_enable = find_val('scheduler', 'enable')
        cur_status = find_val('guild30_config', 'user_status')
        cur_partner = find_val('guild30_config', 'partner_config')
        if cur_enable is True and cur_status == target_status and cur_partner == self.config.config_name:
            logger.info(f'[Guild30] Partner {partner} guild30 already configured as {target_status}, no patch needed')
            return

        def set_arg(group: str, argument: str, types: str, value):
            try:
                r = requests.put(f'{SERVER}/{partner}/Guild30Team/{group}/{argument}/value',
                                 params={'types': types, 'value': value}, timeout=5)
                if r.status_code == 200:
                    logger.info(f'[Guild30] Partner {partner} {group}.{argument} set to {value}')
                else:
                    logger.warning(f'[Guild30] Partner {partner} {group}.{argument} set failed: HTTP {r.status_code}')
            except Exception as e:
                logger.warning(f'[Guild30] Partner {partner} {group}.{argument} set failed: {e}')

        logger.info(f'[Guild30] Patch partner {partner} guild30 as {target_status} '
                    f'(enable={cur_enable}, status={cur_status}, partner={cur_partner})')
        set_arg('scheduler', 'enable', 'boolean', True)
        set_arg('guild30_config', 'user_status', 'string', target_status)
        set_arg('guild30_config', 'partner_config', 'string', self.config.config_name)
        # 副本/次数以主号(发起方)配置为准, 同步给搭档保证两边一致
        set_arg('guild30_config', 'dungeon_type', 'string', conf.dungeon_type.value)
        set_arg('guild30_config', 'battle_count', 'integer', conf.battle_count)

    # ------------------------- 通信辅助 (HTTP :22288) -------------------------

    def _query_partner_running(self, partner: str) -> bool:
        try:
            resp = requests.get(f'{SERVER}/{partner}/process_state', timeout=5)
            if resp.status_code == 200:
                return bool(resp.json().get('running', False))
        except Exception as e:
            logger.warning(f'Query partner {partner} state failed: {e}')
        return False

    def _start_partner(self, partner: str) -> bool:
        try:
            resp = requests.get(f'{SERVER}/{partner}/start', timeout=5)
            if resp.status_code == 200:
                logger.info(f'Partner {partner} start request sent')
                return True
        except Exception as e:
            logger.error(f'Start partner {partner} failed: {e}')
        return False

    def _notify_partner(self, partner: str) -> bool:
        try:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            resp = requests.put(f'{SERVER}/{partner}/Guild30Team/sync_next_run',
                                params={'target_dt': now}, timeout=5)
            if resp.status_code == 200:
                logger.info(f'Partner {partner} notified to run Guild30Team immediately')
                return True
            logger.warning(f'Notify partner {partner} returned {resp.status_code}')
        except Exception as e:
            logger.error(f'Notify partner {partner} failed: {e}')
        return False

    @staticmethod
    def _read_ack() -> dict:
        try:
            return json.loads(ACK_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}

    # ============================== 阶段2: 组队刷本 ==============================

    def _run_farm(self, conf) -> bool:
        """动态加载 Orochi/EvoZone 任务, 用其 run_leader/run_member 复用全部组队刷逻辑。
        以队长配置为唯一权威源: 队员侧用 request 里的 dungeon + count 覆盖自己配置。
        (8-04 修复: 原来只有 count 跟随队长, dungeon 两边各用各的 —— 队长御魂/队员觉醒时
         两人进不同组队界面永远组不上队, 双方超时死循环)"""
        # 副本/次数: 队长用自己配置; 队员完全跟随队长 request (按主号配置)
        dungeon_type = conf.dungeon_type
        battle_count = conf.battle_count
        if conf.user_status == Guild30UserStatus.MEMBER and self._request:
            battle_count = int(self._request.get('count', conf.battle_count))
            try:
                dungeon_type = DungeonType(self._request.get('dungeon', conf.dungeon_type.value))
            except ValueError:
                # request 里的 dungeon 非法(如手改文件): 回退自己配置, 不炸任务
                logger.warning(f'[Guild30] Request dungeon invalid ({self._request.get("dungeon")}), '
                               f'use own config {conf.dungeon_type.value}')
                dungeon_type = conf.dungeon_type

        try:
            task = self._load_farm_task(dungeon_type)
            task.current_count = 0
            task.limit_count = battle_count
            # 1 小时上限, 30 场约 15-30 分钟, 余量充足
            task.limit_time = timedelta(hours=1)
        except Exception as e:
            logger.error(f'Load farm task failed: {e}')
            return False

        # 挂战斗保护 (战斗期间 60s->300s)
        self.device.stuck_record_clear()
        self.device.stuck_record_add('BATTLE_STATUS_S')
        try:
            if conf.user_status == Guild30UserStatus.LEADER:
                logger.info(f'[Guild30] Start farm as LEADER, dungeon={dungeon_type.value}, count={battle_count}')
                # 8-04 双确认第2环: 队长开战前确认搭档在房间(joined 回报)才允许点挑战,
                # 防止路人占位/搭档掉线时开打 —— 这就是"开始计数"的门禁
                return bool(task.run_leader(confirm_fire=lambda: self._partner_in_room()))
            else:
                logger.info(f'[Guild30] Start farm as MEMBER, dungeon={dungeon_type.value} '
                            f'(from leader request), count={battle_count}')
                # 8-04 双确认第2环: 队员每次检测到自己已在房间就回报 joined, 供队长开战前确认
                return bool(task.run_member(on_in_room=self._write_joined))
        except (GameStuckError, TaskEnd):
            # 卡死保护(触发 Restart 重启游戏) 和任务正常结束必须冒泡到调度器,
            # 不能被 except Exception 吞掉 —— 否则卡死后不会重启游戏
            raise
        except Exception as e:
            logger.error(f'Farm error: {e}')
            return False
        finally:
            self.device.stuck_record_clear()

    def _write_joined(self):
        """队员侧: 检测到自己在房间时回报 {ts, request_id} 到 joined.json (覆盖写)。
        队长开战前用 _partner_in_room 检查: request_id 是本次任务的 + ts 新鲜才放行。
        固定文件覆盖写, 不会无限增长; 旧任务残留靠 request_id 匹配自动忽略。
        日志降噪: 每轮循环(约3s)都会调用, 只在 request_id 变化(首次/新任务)时打 info,
        失败打 warning; 成功后静默, 避免刷屏。"""
        try:
            request_id = self._request.get('request_id', '') if self._request else ''
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            JOINED_FILE.write_text(json.dumps({'ts': time.time(), 'request_id': request_id}),
                                   encoding='utf-8')
            if request_id != getattr(self, '_last_joined_rid', None):
                logger.info(f'[Guild30] Joined report written: request_id={request_id[:8]}, '
                            f'member is in room now')
                self._last_joined_rid = request_id
        except Exception as e:
            logger.warning(f'[Guild30] Write joined file failed: {e}')

    def _partner_in_room(self) -> bool:
        """队长侧: 开战前确认搭档在房间。
        读 joined.json: request_id 匹配本次任务 + ts 在 JOINED_FRESH_SECONDS 内 -> 放行。
        返回 False 时 run_invite 不点挑战, 继续等待/重新邀请。
        日志: 每次调用(仅房间满员待开战时)都打结果与原因, 便于无图定位。"""
        if not self._request:
            logger.warning('[Guild30] Partner confirm: no local request (handshake not done), deny fire')
            return False
        try:
            joined = json.loads(JOINED_FILE.read_text(encoding='utf-8'))
        except Exception:
            logger.warning('[Guild30] Partner confirm: joined file missing/unreadable, deny fire')
            return False
        if joined.get('request_id') != self._request.get('request_id'):
            logger.warning(f'[Guild30] Partner confirm: joined request_id mismatch '
                           f'(joined={str(joined.get("request_id",""))[:8]}, '
                           f'want={str(self._request.get("request_id",""))[:8]}), deny fire')
            return False  # 是旧任务/其他对的残留
        ts = joined.get('ts', 0)
        if not isinstance(ts, (int, float)) or time.time() - ts > JOINED_FRESH_SECONDS:
            age = int(time.time() - ts) if isinstance(ts, (int, float)) else -1
            logger.warning(f'[Guild30] Partner confirm: joined stale ({age}s > {JOINED_FRESH_SECONDS}s), '
                           f'partner may be offline, deny fire')
            return False
        logger.info(f'[Guild30] Partner confirm passed (age={int(time.time()-ts)}s, '
                    f'request_id={str(joined.get("request_id",""))[:8]}), fire!')
        return True

    def _load_farm_task(self, dungeon_type: DungeonType):
        if dungeon_type == DungeonType.OROCHI:
            from tasks.Orochi.script_task import ScriptTask as OrochiScriptTask
            logger.info('[Guild30] Load farm task: Orochi (御魂)')
            return OrochiScriptTask(self.config, self.device)
        logger.info('[Guild30] Load farm task: EvoZone (觉醒)')
        from tasks.EvoZone.script_task import ScriptTask as EvoZoneScriptTask
        return EvoZoneScriptTask(self.config, self.device)

    # ============================== 阶段3: 领奖 ==============================

    def _claim_reward(self):
        """去寮三十页面领取已完成任务奖励 (复用 CollectiveMissions 的 get_task_reward)"""
        try:
            self.goto_page(page_collective_missions)
            from tasks.CollectiveMissions.script_task import ScriptTask as CollectiveMissionsScriptTask
            cm = CollectiveMissionsScriptTask(self.config, self.device)
            cm.get_task_reward()
            logger.info('Guild30 reward claimed')
            self.goto_page(page_main)
        except Exception as e:
            logger.warning(f'Claim reward failed: {e}')

    # ============================== 收尾 ==============================

    def _finish(self, success: bool, reason: str):
        logger.info(f'Guild30Team finish: success={success}, reason={reason}')
        self.set_next_run(task='Guild30Team', success=success, finish=True)
        raise TaskEnd


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.run()
