# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：pytest 专用 settings。
# mariadb 的 spug 用户仅被授权 spug 库，无法创建 Django 测试库（test_spug），
# 单元测试统一用 sqlite 内存库（用例不依赖 mysql 特性）。
from spug.settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}
