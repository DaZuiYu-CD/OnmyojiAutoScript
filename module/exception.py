class CampaignEnd(Exception):
    pass


class MapDetectionError(Exception):
    pass


class MapWalkError(Exception):
    pass


class MapEnemyMoved(Exception):
    pass


class CampaignNameError(Exception):
    pass


class ScriptError(Exception):
    # This is likely to be a mistake of developers, but sometimes a random issue
    pass


class ScriptEnd(Exception):
    pass


class GameStuckError(Exception):
    pass


class GameBugError(Exception):
    # An error has occurred in Azur Lane game client. Alas is unable to handle.
    # A restart should fix it.
    pass


class GameTooManyClickError(Exception):
    pass


class EmulatorNotRunningError(Exception):
    pass


class GameNotRunningError(Exception):
    pass


class GamePageUnknownError(Exception):
    pass


class RequestHumanTakeover(Exception):
    # Request human takeover
    # Alas is unable to handle such error, probably because of wrong settings.
    pass

class AccountLoginFailed(Exception):
    # 账号登录后"进入游戏"阶段失败(LoginService 已内部重启游戏重试 2 次仍未识别到庭院)。
    # 8-13 新增: 用于 AccountDaily 区分"进游戏失败(直接跳过该账号, 不再重试)"
    # 与"mpay 切号失败(重试 3 次)" —— 避免登录失败走 RequestHumanTakeover 导致整批账号全灭。
    pass

class TaskEnd(Exception):
    pass
