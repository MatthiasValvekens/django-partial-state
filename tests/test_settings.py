from os.path import dirname, abspath, join

SECRET_KEY = 'fake-key'

DEBUG = True

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'partial_state', 'tests',
]

DATABASES = {
    'default': {
        'NAME': ':memory:',
        'ENGINE': 'django.db.backends.sqlite3'
    }
}

ROOT_URLCONF = 'tests.urls'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

USE_TZ = True

BASE_DIR = dirname(dirname(abspath(__file__)))
