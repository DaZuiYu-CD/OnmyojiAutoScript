# This Python file uses the following encoding: utf-8
# AccountDaily 账号日常任务 —— 配置模型
# 2026-08-07 v2 需求变更版:
#   1. 一个 config 配置多个账号, 每个账号配置各自的任务清单(动态槽位)
#   2. 任务参数全局共享: 每个任务在 config 里只有一份参数, 所有跑它的账号共用
#   3. 仿 FindJade 的 validator/serializer 模式: account_list 与前端扁平化键互转
#
# config 结构铁律(8-05 两连炸教训): 普通字段必须放嵌套子模型(account_count 在 AccountDailyConfig),
# 禁止放任务模型顶层, 否则 config_model.extract_groups 遍历顶层 properties 时 KeyError('$ref') 后端重启即炸。

from enum import Enum
from typing import Any, Dict
from datetime import datetime

from pydantic import Field, BaseModel, model_validator, model_serializer, ValidationError

from deploy.logger import logger
from tasks.Component.config_base import ConfigBase, DateTime
from tasks.Component.config_scheduler import Scheduler


class TaskName(str, Enum):
    """
    可选任务枚举。
    枚举值 = 任务目录名(大驼峰) = tasks/<Task>/script_task.py 的目录名, 运行时按此动态加载。
    排除项: AccountDaily(自己) / Restart(系统重启) / Script(脚本配置) / GlobalGame(全局) /
            GuildActivityMonitor(监控任务已废弃, 8-01 用户决策不再启用)
    注意: 新增任务时在此加一行, 否则前端下拉里选不到。
    注意: 字段类型必须是非 Optional 的枚举(带默认值), 不能用 X | None ——
          Optional 的 schema 是 anyOf, 前端提取 enumEnum 只认顶层 $ref, 拿不到下拉选项(8-07 实测)。
          空槽位用 Empty 成员占位, get_task_list 跳过。
    """
    # 空槽位占位: 下拉第一项, 表示该槽位不执行任务
    Empty = 'None'
    # ---- 每日任务 ----
    DailyTrifles = 'DailyTrifles'          # 每日事务
    WantedQuests = 'WantedQuests'          # 悬赏
    Exploration = 'Exploration'            # 探索
    GoldYoukai = 'GoldYoukai'              # 金币妖怪
    ExperienceYoukai = 'ExperienceYoukai'  # 经验妖怪
    TalismanPass = 'TalismanPass'          # 逢魔密信(御灵?)
    Pets = 'Pets'                          # 宠物
    SoulsTidy = 'SoulsTidy'                # 御魂整理
    Delegation = 'Delegation'              # 委托
    Tako = 'Tako'                          # 年兽/石距
    Nian = 'Nian'                          # 年兽
    RealmRaid = 'RealmRaid'                # 结界突破
    RyouToppa = 'RyouToppa'                # 寮突破
    KekkaiUtilize = 'KekkaiUtilize'        # 结界寄养
    KekkaiActivation = 'KekkaiActivation'  # 结界激活
    DemonEncounter = 'DemonEncounter'      # 逢魔之时
    AreaBoss = 'AreaBoss'                  # 地域鬼王
    AutoCheckinBigGod = 'AutoCheckinBigGod'  # 自动签到/大天狗
    # ---- 御魂 ----
    Orochi = 'Orochi'                      # 御魂八岐大蛇
    OrochiMoans = 'OrochiMoans'            # 御魂悲鸣
    Sougenbi = 'Sougenbi'                  # 业原火
    FallenSun = 'FallenSun'                # 日轮之陨
    EternitySea = 'EternitySea'            # 永生之海
    SixRealms = 'SixRealms'                # 六道之门
    OtherWorldTwilight = 'OtherWorldTwilight'  # 日曜阁?
    # ---- 活动 ----
    ActivityShikigami = 'ActivityShikigami'  # 活动式神
    MetaDemon = 'MetaDemon'                # 超鬼王
    FrogBoss = 'FrogBoss'                  # 呱太首领
    FloatParade = 'FloatParade'            # 花车巡游
    Quiz = 'Quiz'                          # 问答
    KittyShop = 'KittyShop'                # 猫咪商店
    DyeTrials = 'DyeTrials'                # 染色试炼
    GuguArtStudio = 'GuguArtStudio'        # 姑姑艺术工坊
    # ---- 肝帝专属 ----
    BondlingFairyland = 'BondlingFairyland'  # 结缘之境
    EvoZone = 'EvoZone'                    # 御魂进化?
    GoryouRealm = 'GoryouRealm'            # 御灵之境
    Hyakkiyakou = 'Hyakkiyakou'            # 百鬼夜行
    HeroTest = 'HeroTest'                  # 式神试炼
    FindJade = 'FindJade'                  # 寻找协作任务
    MemoryScrolls = 'MemoryScrolls'        # 绘卷
    InfiniteBattle = 'InfiniteBattle'      # 无限战斗
    # ---- 每周任务 ----
    TrueOrochi = 'TrueOrochi'              # 真八岐大蛇
    RichMan = 'RichMan'                    # 富豪
    Secret = 'Secret'                      # 秘闻
    WeeklyTrifles = 'WeeklyTrifles'        # 每周事务
    MysteryShop = 'MysteryShop'            # 神秘商店
    Duel = 'Duel'                          # 斗技
    # ---- 阴阳寮 ----
    CollectiveMissions = 'CollectiveMissions'  # 集体任务
    Guild30Team = 'Guild30Team'            # 寮30
    Hunt = 'Hunt'                          # 狩猎
    Dokan = 'Dokan'                        # 狭间
    AbyssShadows = 'AbyssShadows'          # 深渊暗影
    GuildBanquet = 'GuildBanquet'          # 宴会
    DemonRetreat = 'DemonRetreat'          # 退治


# 每号任务槽位上限: 模型必须静态声明(否则 schema 无字段 → 前端渲染不出),
# serializer 按 task_count 裁剪输出 → 前端只显示 N 个槽位(动态效果, 见 serializer_task_slots)
TASK_SLOT_MAX = 12


class AccountDailyItem(ConfigBase):
    """
    单个账号及其独立任务清单。
    注意: 不复用 SwitchAccount 的 AccountInfo(公共模型, 禁改), 这里自建同构字段。
    svr 建议每号都填: login() 先走角色名 OCR(switch_character), 失败且 svr 非空才走 switch_svr 兜底。
    """
    character: str = Field(default='', description='character_help')
    svr: str = Field(default='', description='svr_help')
    account: str = Field(default='', description='account_help')
    # 为防止 OCR 出错, 多个别名以 # 分隔
    account_alias: str = Field(default='', description='account_alias_help')
    apple_or_android: bool = Field(default=True, description='apple_or_android_help')
    # 是否切换服务器(8-14 新增, 用户优先需求):
    #   True  = 走完整角色/服务器匹配(switch_character 四态 + switch_svr 兜底)
    #   False = 切好账号后不展开服务器/角色列表, 直接用账号默认角色进入游戏。
    #           依赖前提: 账号每次用完都停在目标角色上(用户已确认成立)。
    #           为什么需要: 8-10 实测同名角色跨服(音起时/砂狐乐园各一个"花尾巴狗"),
    #           角色列表顺序匹配会点错进回归号; 单角色账号大部分场景根本不需要选角色。
    switch_svr: bool = Field(default=True, description='switch_svr_help')
    # 该号任务数量: 前端/序列化用它决定输出多少个 task_N 槽位(仿 FindJade 的 sup_account_count 思路)
    task_count: int = Field(default=1, ge=1, le=TASK_SLOT_MAX, description='task_count_help')
    # ---- 任务槽位(静态声明上限, 空槽=Empty 跳过) ----
    task_1: TaskName = Field(default=TaskName.Empty, description='task_1_help')
    task_2: TaskName = Field(default=TaskName.Empty, description='task_2_help')
    task_3: TaskName = Field(default=TaskName.Empty, description='task_3_help')
    task_4: TaskName = Field(default=TaskName.Empty, description='task_4_help')
    task_5: TaskName = Field(default=TaskName.Empty, description='task_5_help')
    task_6: TaskName = Field(default=TaskName.Empty, description='task_6_help')
    task_7: TaskName = Field(default=TaskName.Empty, description='task_7_help')
    task_8: TaskName = Field(default=TaskName.Empty, description='task_8_help')
    task_9: TaskName = Field(default=TaskName.Empty, description='task_9_help')
    task_10: TaskName = Field(default=TaskName.Empty, description='task_10_help')
    task_11: TaskName = Field(default=TaskName.Empty, description='task_11_help')
    task_12: TaskName = Field(default=TaskName.Empty, description='task_12_help')
    # 上一次执行完成时间, 用于前端展示"上次跑过没"
    last_complete_time: DateTime = Field(default=DateTime.fromisoformat('2023-01-01 00:00:00'),
                                         description='last_complete_time_help')

    @model_validator(mode='before')
    @classmethod
    def validator_task_slots(cls, v: Any) -> Any:
        """
        读入时按 task_count 补齐缺失槽位为 Empty。
        原因: config json 里可能只有 task_1..task_{count}, 缺失/为 null 的槽位必须补占位值,
        否则 getattr(item, 'task_5') 会 AttributeError / TaskName 字段收到 null 报 ValidationError。
        """
        if not isinstance(v, dict):
            return v
        n = v.get('task_count', 1)
        for i in range(1, TASK_SLOT_MAX + 1):
            val = v.get(f'task_{i}')
            # null/空串/缺失 统一转为 Empty 占位
            if val is None or val == '' or val == 'None':
                v[f'task_{i}'] = 'None'
        return v

    @model_serializer()
    def serializer_task_slots(self) -> Dict[str, Any]:
        """
        只输出 task_1..task_{task_count} 以及基本信息, 实现"动态槽位":
        - 前端下次加载参数时只看到 N 个任务下拉(而不是 12 个空槽)
        - datetime 手动转标准字符串('YYYY-MM-DD HH:MM:SS', 与 DateTime PlainSerializer 一致),
          因为 model_serializer 完全接管序列化, 不会自动应用字段级 serializer
        - TaskName(str,Enum) 转 .value, 保证 json 里是纯字符串
        """
        data: Dict[str, Any] = {}
        for key, value in self.__dict__.items():
            # 裁剪超出 task_count 的槽位
            if key.startswith('task_') and not key.startswith('task_count'):
                try:
                    idx = int(key.split('_')[1])
                except (IndexError, ValueError):
                    idx = 0
                if idx > self.task_count:
                    continue
            if isinstance(value, datetime):  # DateTime 底层就是 datetime, 手动转标准字符串
                data[key] = value.strftime('%Y-%m-%d %H:%M:%S')
            elif isinstance(value, TaskName):
                data[key] = value.value
            elif isinstance(value, BaseModel):
                data[key] = value.model_dump()
            else:
                data[key] = value
        return data

    def get_task_list(self) -> list:
        """
        该号的任务清单: 按配置顺序取非空槽位并去重(同一任务配两次只执行一次)。
        :return: 任务目录名(大驼峰)列表
        """
        result = []
        for i in range(1, self.task_count + 1):
            t = getattr(self, f'task_{i}', None)
            if t is None or t == TaskName.Empty:
                continue
            name = t.value if isinstance(t, TaskName) else str(t)
            if name and name != 'None' and name not in result:
                result.append(name)
        return result

    def is_valid(self) -> bool:
        """角色名非空才算有效账号(与 AccountInfo.is_valid 语义一致, 角色名是切号的识别依据)"""
        return bool(self.character)


class AccountDailyConfig(ConfigBase):
    """
    账号数量配置: 前端填数字后, 配合 AccountDaily.validator_all 自动生成对应数量的
    account_list_N 配置项(仿 FindJade 的 sup_account_count 机制, 已验证可用)。
    """
    account_count: int = Field(default=1, ge=1, le=24, description='account_count_help')


class AccountDaily(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    account_daily_config: AccountDailyConfig = Field(default_factory=AccountDailyConfig)
    # 账号列表: 前端渲染为 account_list_1/2/3... 扁平化键
    account_list: list[AccountDailyItem] = None

    @model_validator(mode='before')
    @classmethod
    def validator_all(cls, v: Any) -> Any:
        """
        将扁平化字段 account_list_1/2/3... 重组为 account_list 列表(仿 FindJade validator_all)。
        前端保存的是扁平键, 反序列化时 pydantic 需要真实列表。
        """
        if not isinstance(v, dict):
            return v
        account_count = v.get('account_daily_config', {}).get('account_count', 1)

        def validator_list(list_name, data, item_type=None, list_size=1):
            if list_name not in data:
                data[list_name] = []
            remove_keys = []
            for key, value in data.items():
                if list_name == key or list_name not in key:
                    continue
                try:
                    item = item_type(**value)
                    if item.is_valid():
                        data[list_name].append(item)
                    remove_keys.append(key)
                except ValidationError as e:
                    pass
                except TypeError as e:
                    pass
            for key in remove_keys:
                del data[key]
            # 补齐到 list_size, 保证前端"填数字就出槽位"
            if item_type is not None:
                if len(data[list_name]) < list_size:
                    for i in range(list_size - len(data[list_name])):
                        data[list_name].append(item_type())

        validator_list('account_list', v, AccountDailyItem, account_count)
        return v

    @model_serializer()
    def serializer_model(self, value: Any) -> Dict[str, Any]:
        """
        账号列表序列化为 account_list_1/2/3... 扁平键供前端渲染(仿 FindJade serializer_model)。
        列表内每项再走 AccountDailyItem 自己的 serializer(裁剪 task 槽位)。
        """
        properties = self.__dict__
        data: Dict[str, Any] = {}

        def v_dump(v):
            try:
                return v.model_dump()
            except AttributeError as e:
                logger.error(e)
                return v

        for key, value in properties.items():
            if isinstance(value, list):
                for index, v in enumerate(value):
                    data[f'{key}_{index + 1}'] = v_dump(v)
            else:
                data[key] = v_dump(value)
        return data

    def update_account_login_history(self, account: AccountDailyItem):
        """记录某账号本次完成时间(仿 FindJade update_account_login_history)"""
        accountInfoList = self.account_list
        if not accountInfoList:
            return
        for info in accountInfoList:
            if info.character != account.character or info.svr != account.svr:
                continue
            # 直接给 datetime 对象赋值, pydantic 会经 DateTime 校验/序列化, 无需手动转字符串
            info.last_complete_time = datetime.now()
            break
