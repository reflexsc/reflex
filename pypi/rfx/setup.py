from setuptools import setup
setup(
  name = 'rfx',
  packages = ['rfx'],
  version = "1802.0004",
  description = 'Reflex - Container Config and Secret Management - core lib',
  author = 'Brandon Gillespie',
  author_email = 'bjg-pypi@cold.org',
  url = 'https://reflex.cold.org/',
  keywords = ['docker','config','secrets'],
  install_requires = [
    'urllib3',
    'dateparser',
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

