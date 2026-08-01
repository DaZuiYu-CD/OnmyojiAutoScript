# This Python file uses the following encoding: utf-8
# 无限战斗模式: 用户已就位(选好副本/组好队), 脚本只循环 挑战->战斗->结算
from enum import Enum

from pydantic import Field

from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig


class UserStatus(str, Enum):
    LEADER = 'leader'
    MEMBER = 'member'
    ALONE = 'alone'


class ChallengeButton(str, Enum):
    # 御魂房间挑战(房间内挑战按钮 + 御魂副本页挑战按钮)
    OROCHI = 'orochi'
    # 爬塔/活动挑战(活动通用挑战按钮)
    ACTIVITY = 'activity'
    # 自定义图片, 放到 tasks/InfiniteBattle/res/ 下并在 custom_challenge_path 填文件名
    CUSTOM = 'custom'


class InfiniteBattleConfig(ConfigBase):
    # 身份
    user_status: UserStatus = Field(default=UserStatus.ALONE, description='ib_user_status_help')
    # 限制时间
    limit_time: Time = Field(default=Time(hour=1), description='ib_limit_time_help')
    # 限制次数
    limit_count: int = Field(default=999, description='ib_limit_count_help')
    # 挑战按钮类型
    challenge_button: ChallengeButton = Field(default=ChallengeButton.OROCHI, description='ib_challenge_button_help')
    # 自定义挑战按钮图片文件名(tasks/InfiniteBattle/res/ 目录下)
    custom_challenge_path: str = Field(default='', description='ib_custom_challenge_path_help')
    # 是否接受协作/封印邀请
    accept_invite_enable: bool = Field(default=True, description='ib_accept_invite_enable_help')
    # 队长战后是否勾选默认邀请队友
    default_invite: bool = Field(default=True, description='ib_default_invite_help')
    # 无进展判异常秒数
    no_progress_timeout: int = Field(default=180, description='ib_no_progress_timeout_help', ge=30, le=1800)


class InfiniteBattle(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    infinite_battle_config: InfiniteBattleConfig = Field(default_factory=InfiniteBattleConfig)
    general_battle_config: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
