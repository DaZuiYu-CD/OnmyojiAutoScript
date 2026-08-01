# This Python file uses the following encoding: utf-8
"""常用任务(收藏)标记的独立存储。

存储在 config/favorites.json, 与脚本配置 JSON 分离:
- 脚本配置读写都过 pydantic 模型, 未知字段会在 auto save 时被抹掉
- 独立文件不受配置保存周期影响, 也不污染任务参数表单

结构: { "<config_name>": ["<task_gui_name>", ...] }
task 以前端 API 使用的 GUI 名称原样存储(与 /{script}/{task}/ 系列端点一致)。
"""
from pathlib import Path

from module.config.utils import read_file, write_file
from module.server.config_manager import ConfigManager


def _store_path() -> Path:
    return ConfigManager.config_dir() / 'favorites.json'


def _load() -> dict:
    data = read_file(str(_store_path()))
    if not isinstance(data, dict):
        return {}
    return data


def _save(data: dict) -> None:
    write_file(str(_store_path()), data)


def favorites_of(config: str) -> list:
    """返回指定配置的常用任务名列表(保持标记顺序)。"""
    value = _load().get(config, [])
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def set_favorite(config: str, task: str, favorite: bool) -> list:
    """设置/取消常用标记, 返回更新后的列表。"""
    data = _load()
    tasks = data.get(config, [])
    if not isinstance(tasks, list):
        tasks = []
    tasks = [str(item) for item in tasks]
    if favorite and task not in tasks:
        tasks.append(task)
    elif not favorite and task in tasks:
        tasks.remove(task)
    data[config] = tasks
    _save(data)
    return tasks
