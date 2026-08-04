# This Python file uses the following encoding: utf-8
# @author runhey
# Guild30Team 大小号联动刷寮三十
# 本任务不定义自己的识别资产, 全部复用:
#   - 组队界面: OrochiAssets / EvoZoneAssets (I_FORM_TEAM/I_CHECK_TEAM 等)
#   - 领奖: CollectiveMissionsAssets (I_CM_GET_REWARD/I_CM_CLOSE 等)
#   - 房间/邀请: GeneralRoomAssets / GeneralInviteAssets
from tasks.Orochi.assets import OrochiAssets
from tasks.EvoZone.assets import EvoZoneAssets
from tasks.CollectiveMissions.assets import CollectiveMissionsAssets
from tasks.Component.GeneralRoom.assets import GeneralRoomAssets
from tasks.Component.GeneralInvite.assets import GeneralInviteAssets


class Guild30TeamAssets(GeneralRoomAssets, GeneralInviteAssets, OrochiAssets, EvoZoneAssets, CollectiveMissionsAssets):
    pass
