from adbutils import device

from module.config.config import Config
from module.device.device import Device
from module.exception import AccountLoginFailed, RequestHumanTakeover
from tasks.Component.SwitchAccount.assets import SwitchAccountAssets
from tasks.Component.SwitchAccount.exit_game import ExitGame
from tasks.Component.SwitchAccount.login_account import LoginAccount
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.Login.service import LoginService
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_login

from module.logger import logger


class SwitchAccount(LoginAccount, ExitGame, GameUi, SwitchAccountAssets):

    def __init__(self, config: Config, device: Device, to: AccountInfo, frm: AccountInfo = None):
        """

        @param config:
        @type config:
        @param device:
        @type device:
        @param to: 要登录的账号信息
        @type to:
        @param frm: 上一个账号信息 ,避免关键字from
        @type frm:
        """
        super().__init__(config, device)
        self.to_account_info = to
        self.from_account_info = frm

    def switchAccount(self):
        logger.info("start switchAccount %s-%s", self.to_account_info.character, self.to_account_info.svr)
        # 判断所处界面
        curPage = self.get_current_page()

        if curPage != page_login and curPage != page_main:
            self.goto_page(page_main)
            curPage = self.get_current_page()
        if curPage == page_main:
            self.exitGame()

        # 处于登录界面
        if not self.login(self.to_account_info):
            return False
        logger.info("%s login suc", self.to_account_info.character)
        # 处理位于登录界面各种奇葩弹窗 + 等待进入庭院
        login_handler = LoginService(config=self.config, device=self.device)
        login_handler.set_specific_usr(self.to_account_info.svr)
        try:
            login_handler.app_handle_login()
        except RequestHumanTakeover as e:
            # 进入游戏失败: LoginService 内部已"重启游戏重试 2 次"仍未识别到庭院(8-13 改造)。
            # 原逻辑直接抛 RequestHumanTakeover → AccountDaily 透传给调度器 → 整批账号全灭(8-10 实测)。
            # 现在转成 AccountLoginFailed, AccountDaily 捕获后跳过该账号, 后续账号继续跑。
            logger.error('switchAccount: 账号 %s-%s 进入游戏失败(已重启游戏重试2次), 跳过该账号: %s',
                         self.to_account_info.character, self.to_account_info.svr, e)
            raise AccountLoginFailed('account %s-%s enter game failed' % (
                self.to_account_info.character, self.to_account_info.svr))

        return True


if __name__ == '__main__':
    config = Config('oas1')
    device=Device()
    toAccount=AccountInfo(account="email0@163.com", account_alias="emailO#emailo", apple_or_android=True, character="粘贴", svr="立秋夕烛")
    sa=SwitchAccount(config,device,toAccount)
    sa.switchAccount()
