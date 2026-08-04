# This Python file uses the following encoding: utf-8
# @author runhey
# 大小号联动刷寮三十 (Guild30Team)
# 设计: 号1(队长)与号2(队员)通过共享状态文件握手后, 各自进入御魂/觉醒组队界面,
#       队长开房邀请, 队员接受邀请入队, 组队循环刷 battle_count 次 (必须组队才算),
#       刷完后各自回寮三十领奖。任何环节失败一律跳过任务, 不单刷、不卡死。
from enum import Enum
from pydantic import BaseModel, Field

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase


class DungeonType(str, Enum):
    """刷什么副本"""
    OROCHI = '御魂'
    EVOZONE = '觉醒'


class Guild30UserStatus(str, Enum):
    """联动身份: 队长开房, 队员入队"""
    LEADER = '队长'
    MEMBER = '队员'


class Guild30Config(ConfigBase):
    # 刷什么副本 (御魂/觉醒), 与 Orochi/EvoZone 任务共用同一组队界面入口
    dungeon_type: DungeonType = Field(default=DungeonType.OROCHI, description='dungeon_type_help')
    # 组队战斗次数 (默认30), 队长每开一场双方各+1
    battle_count: int = Field(default=30, ge=1, le=300, description='battle_count_help')
    # 搭档账号 config 名 (如 '主号' / '沙湖小号2'), 通信/拉起均按此名
    partner_config: str = Field(default='主号', description='partner_config_help')
    # 联动身份: 队长(开房) / 队员(入队); 若两号配置相同, 号1自动为队长
    user_status: Guild30UserStatus = Field(default=Guild30UserStatus.MEMBER, description='user_status_help')
    # 刷完后是否去寮三十领取已完成任务奖励
    claim_reward: bool = Field(default=True, description='claim_reward_help')
    # 搭档未启动时是否自动拉起 (HTTP /{partner}/start)
    start_partner: bool = Field(default=True, description='start_partner_help')
    # 等搭档就绪 (ack) 超时秒数
    ack_timeout: int = Field(default=180, ge=30, le=600, description='ack_timeout_help')


class Guild30Team(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    guild30_config: Guild30Config = Field(default_factory=Guild30Config)
    # 注意: 战斗/邀请参数(层数、队伍、buff、邀请好友等)不在此配置,
    # 动态加载 Orochi/EvoZone 任务时复用账号自己 orochi/evo_zone 任务的配置 —— 按主号配置原则
