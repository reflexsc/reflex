from setuptools import setup
setup(
  name = 'rfx',
  packages = ['rfx'],
  version = "1707.0009",
  description = 'Container Config and Secret Management - core lib',
  author = 'Brandon Gillespie',
  author_email = 'bjg-pypi@cold.org',
  url = 'https://reflex.cold.org/',
  keywords = ['docker','config','secrets'],
  install_requires = [
    'urllib3',
    'ujson',
    'pynacl',
    'requests',
    'setproctitle',
    'dictlib',
    'onetimejwt',
    'timeinterval'
  ],
  classifiers = [],
)

