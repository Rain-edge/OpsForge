import os

# OpsForge：官方 entrypoint 首启生成 overrides.py 的 env 化版本。
# 镜像已内置代码（构建期 clone，不走首启 clone 分支），此文件随镜像提供。
# SECRET_KEY 从环境变量读取，缺省值仅限本地实验环境，不得用于任何对外服务。

DEBUG = False
ALLOWED_HOSTS = ['127.0.0.1']
SECRET_KEY = os.environ.get('SPUG_SECRET_KEY', 'opsforge-local-lab-only')

DATABASES = {
    'default': {
        'ATOMIC_REQUESTS': True,
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('MYSQL_DATABASE'),
        'USER': os.environ.get('MYSQL_USER'),
        'PASSWORD': os.environ.get('MYSQL_PASSWORD'),
        'HOST': os.environ.get('MYSQL_HOST'),
        'PORT': os.environ.get('MYSQL_PORT'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'sql_mode': 'STRICT_TRANS_TABLES',
        }
    }
}
