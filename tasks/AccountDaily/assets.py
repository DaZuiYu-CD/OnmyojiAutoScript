# This Python file uses the following encoding: utf-8
# AccountDaily 账号日常 —— 资产类(空)
# 任务本身不需要识别图(切号复用 SwitchAccount 的资产, 子任务各自带自己的资产),
# 但任务结构要求必须存在 assets.py, 否则部分模板匹配/加载逻辑可能缺文件。

from tasks.base_task import BaseTask


class AccountDailyAssets(BaseTask):
    pass
